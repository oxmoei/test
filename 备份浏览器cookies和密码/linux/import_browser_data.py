# -*- coding: utf-8 -*-
"""
Linux 浏览器数据导入工具
功能：将加密备份的 Cookies 和密码导入到浏览器
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 确认导入文件来源可信
  3. 导入前备份当前浏览器数据
"""

import os
import json
import base64
import sqlite3
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import getpass

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


class BrowserDataImporter:
    """Linux 浏览器数据导入器"""
    
    def __init__(self):
        home = os.path.expanduser('~')
        self.browsers = {
            "Chrome": os.path.join(home, ".config/google-chrome/Default"),
            "Chromium": os.path.join(home, ".config/chromium/Default"),
            "Brave": os.path.join(home, ".config/BraveSoftware/Brave-Browser/Default"),
            "Edge": os.path.join(home, ".config/microsoft-edge/Default"),
        }
        self.exports_dir = Path(__file__).parent / "exports"
    
    def decrypt_import_data(self, encrypted_data, password):
        """解密导入数据"""
        try:
            # 提取加密组件
            salt = base64.b64decode(encrypted_data["salt"])
            nonce = base64.b64decode(encrypted_data["nonce"])
            tag = base64.b64decode(encrypted_data["tag"])
            ciphertext = base64.b64decode(encrypted_data["ciphertext"])
            
            # 重新生成密钥
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            
            # 解密数据
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            
            return json.loads(plaintext.decode('utf-8'))
        except Exception as e:
            print(f"❌ 解密数据失败: {e}")
            return None
    
    def get_master_key(self, browser_name):
        """获取浏览器主密钥（从 Linux Keyring）"""
        try:
            # 方法 1：尝试使用 secretstorage 库
            try:
                import secretstorage
                connection = secretstorage.dbus_init()
                collection = secretstorage.get_default_collection(connection)
                
                keyring_labels = {
                    "Chrome": "Chrome Safe Storage",
                    "Chromium": "Chromium Safe Storage",
                    "Brave": "Brave Safe Storage",
                    "Edge": "Chromium Safe Storage",
                }
                
                label = keyring_labels.get(browser_name, "Chrome Safe Storage")
                
                for item in collection.get_all_items():
                    if item.get_label() == label:
                        password = item.get_secret().decode('utf-8')
                        connection.close()
                        
                        salt = b'saltysalt'
                        iterations = 1
                        key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
                        return key
                
                connection.close()
            except Exception as e:
                pass
            
            # 方法 2：尝试使用 libsecret-tool 命令行工具
            try:
                keyring_apps = {
                    "Chrome": "chrome",
                    "Chromium": "chromium",
                    "Brave": "brave",
                    "Edge": "chromium",
                }
                
                app = keyring_apps.get(browser_name, "chrome")
                cmd = ['secret-tool', 'lookup', 'application', app]
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    password = result.stdout.strip()
                    salt = b'saltysalt'
                    iterations = 1
                    key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
                    return key
            except Exception as e:
                pass
            
            # 方法 3：使用默认密码
            print(f"⚠️  使用默认密钥（密钥环不可用或未配置）")
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1
            key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
            return key
            
        except Exception as e:
            print(f"❌ 获取 {browser_name} 主密钥失败: {e}")
            password = "peanuts"
            salt = b'saltysalt'
            iterations = 1
            key = PBKDF2(password.encode('utf-8'), salt, dkLen=16, count=iterations)
            return key
    
    def encrypt_payload(self, plain_text, master_key):
        """加密数据"""
        try:
            from Crypto.Random import get_random_bytes
            from Crypto.Util.Padding import pad
            
            # 使用 AES-128-CBC 模式加密（Linux Chrome v10+）
            iv = b' ' * 16  # Chrome on Linux uses blank IV
            cipher = AES.new(master_key, AES.MODE_CBC, iv)
            
            # PKCS7 padding
            padded_data = pad(plain_text.encode('utf-8'), AES.block_size)
            encrypted_data = cipher.encrypt(padded_data)
            
            # 添加 v10 前缀
            return b'v10' + encrypted_data
        except Exception as e:
            print(f"❌ 加密失败: {e}")
            return None
    
    def import_cookies(self, browser_name, browser_path, cookies, master_key):
        """导入 Cookies"""
        cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            print(f"❌ {browser_name} Cookies 文件不存在")
            return False
        
        # 备份现有 Cookies
        backup_path = cookies_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(cookies_path, backup_path)
            print(f"📦 已备份现有 Cookies 到: {backup_path}")
        except Exception as e:
            print(f"⚠️  备份失败: {e}")
        
        # 导入 Cookies
        success_count = 0
        try:
            conn = sqlite3.connect(cookies_path)
            cursor = conn.cursor()
            
            for cookie in cookies:
                try:
                    # 加密 cookie 值
                    encrypted_value = self.encrypt_payload(cookie["value"], master_key)
                    if not encrypted_value:
                        continue
                    
                    # 检查是否已存在
                    cursor.execute(
                        "SELECT COUNT(*) FROM cookies WHERE host_key=? AND name=?",
                        (cookie["host"], cookie["name"])
                    )
                    exists = cursor.fetchone()[0] > 0
                    
                    if exists:
                        # 更新现有 cookie
                        cursor.execute(
                            "UPDATE cookies SET encrypted_value=?, path=?, expires_utc=?, is_secure=?, is_httponly=? WHERE host_key=? AND name=?",
                            (encrypted_value, cookie["path"], cookie["expires"], cookie["secure"], cookie["httponly"], cookie["host"], cookie["name"])
                        )
                    else:
                        # 插入新 cookie
                        cursor.execute(
                            "INSERT INTO cookies (host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly, creation_utc, last_access_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (cookie["host"], cookie["name"], encrypted_value, cookie["path"], cookie["expires"], cookie["secure"], cookie["httponly"], cookie["expires"], cookie["expires"])
                        )
                    success_count += 1
                except Exception as e:
                    continue
            
            conn.commit()
            conn.close()
            print(f"✅ {browser_name} 成功导入 {success_count}/{len(cookies)} 个 Cookies")
            return True
        except Exception as e:
            print(f"❌ 导入 {browser_name} Cookies 失败: {e}")
            return False
    
    def import_passwords(self, browser_name, browser_path, passwords, master_key):
        """导入密码"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"❌ {browser_name} Login Data 文件不存在")
            return False
        
        # 备份现有密码数据
        backup_path = login_data_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(login_data_path, backup_path)
            print(f"📦 已备份现有密码到: {backup_path}")
        except Exception as e:
            print(f"⚠️  备份失败: {e}")
        
        # 导入密码
        success_count = 0
        try:
            conn = sqlite3.connect(login_data_path)
            cursor = conn.cursor()
            
            for pwd in passwords:
                try:
                    # 加密密码
                    encrypted_password = self.encrypt_payload(pwd["password"], master_key)
                    if not encrypted_password:
                        continue
                    
                    # 检查是否已存在
                    cursor.execute(
                        "SELECT COUNT(*) FROM logins WHERE origin_url=? AND username_value=?",
                        (pwd["url"], pwd["username"])
                    )
                    exists = cursor.fetchone()[0] > 0
                    
                    if exists:
                        # 更新现有密码
                        cursor.execute(
                            "UPDATE logins SET password_value=? WHERE origin_url=? AND username_value=?",
                            (encrypted_password, pwd["url"], pwd["username"])
                        )
                    else:
                        # 插入新密码
                        cursor.execute(
                            "INSERT INTO logins (origin_url, username_value, password_value, date_created, date_last_used) VALUES (?, ?, ?, ?, ?)",
                            (pwd["url"], pwd["username"], encrypted_password, 13313131313131313, 13313131313131313)
                        )
                    success_count += 1
                except Exception as e:
                    continue
            
            conn.commit()
            conn.close()
            print(f"✅ {browser_name} 成功导入 {success_count}/{len(passwords)} 个密码")
            return True
        except Exception as e:
            print(f"❌ 导入 {browser_name} 密码失败: {e}")
            return False
    
    def import_all(self, import_file):
        """导入所有浏览器数据"""
        print("\n" + "="*60)
        print("🔓 Linux 浏览器数据导入工具")
        print("="*60)
        print("⚠️  警告：导入前请确保：")
        print("  1. 关闭所有浏览器窗口")
        print("  2. 已备份当前浏览器数据")
        print("  3. 确认导入文件来源可信")
        print("-"*60)
        
        # 读取加密文件
        if not os.path.exists(import_file):
            print(f"❌ 文件不存在: {import_file}")
            return
        
        with open(import_file, 'r', encoding='utf-8') as f:
            encrypted_data = json.load(f)
        
        # 解密数据
        password = "cookies2026"
        print("\n🔓 使用预设密码解密文件...")
        data = self.decrypt_import_data(encrypted_data, password)
        if not data:
            return
        
        print(f"\n📄 导出信息：")
        print(f"  - 导出时间: {data['export_time']}")
        print(f"  - 导出用户: {data['username']}")
        print(f"  - 平台: {data.get('platform', 'Unknown')}")
        print(f"  - 浏览器数量: {len(data['browsers'])}")
        
        # 确认导入
        confirm = input("\n是否继续导入？(yes/no): ")
        if confirm.lower() != 'yes':
            print("❌ 已取消导入")
            return
        
        # 导入数据
        for browser_name, browser_data in data["browsers"].items():
            if browser_name not in self.browsers:
                print(f"⏭️  跳过 {browser_name}（不支持）")
                continue
            
            browser_path = self.browsers[browser_name]
            if not os.path.exists(browser_path):
                print(f"⏭️  跳过 {browser_name}（未安装）")
                continue
            
            print(f"\n📦 导入 {browser_name}...")
            
            # 获取主密钥
            master_key = self.get_master_key(browser_name)
            if not master_key:
                print(f"❌ 无法获取 {browser_name} 主密钥")
                continue
            
            # 导入 Cookies
            if browser_data["cookies"]:
                self.import_cookies(browser_name, browser_path, browser_data["cookies"], master_key)
            
            # 导入密码
            if browser_data["passwords"]:
                self.import_passwords(browser_name, browser_path, browser_data["passwords"], master_key)
        
        print("\n" + "="*60)
        print("✅ 导入完成！")
        print("\n⚠️  重要提醒：")
        print("  1. 请重启浏览器以应用更改")
        print("  2. 检查导入的数据是否正确")
        print("  3. 建议删除导入文件")
        print("="*60)


def main():
    """主函数"""
    importer = BrowserDataImporter()
    
    # 列出可用的导出文件
    exports_dir = importer.exports_dir
    if not exports_dir.exists():
        print("❌ 未找到导出目录")
        return
    
    export_files = list(exports_dir.glob("browser_data_*.encrypted"))
    if not export_files:
        print("❌ 未找到导出文件")
        return
    
    print("\n可用的导出文件：")
    for i, file in enumerate(export_files, 1):
        print(f"  {i}. {file.name}")
    
    # 选择文件
    try:
        choice = int(input("\n请选择要导入的文件编号: "))
        if 1 <= choice <= len(export_files):
            importer.import_all(export_files[choice - 1])
        else:
            print("❌ 无效的选择")
    except ValueError:
        print("❌ 无效的输入")


if __name__ == "__main__":
    main()
