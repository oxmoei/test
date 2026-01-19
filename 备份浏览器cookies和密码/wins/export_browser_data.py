# -*- coding: utf-8 -*-
"""
windows 浏览器数据导出工具
功能：解密并导出 Chrome/Edge 的 Cookies 和密码为加密备份
警告：此工具处理敏感数据，请确保：
  1. 仅在自己的设备上使用
  2. 导出文件需加密存储
  3. 不要分享导出文件
"""

import os
import json
import base64
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
import getpass

try:
    from win32crypt import CryptUnprotectData
except ImportError:
    print("❌ 需要安装 pywin32: pip install pywin32")
    exit(1)

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Random import get_random_bytes
except ImportError:
    print("❌ 需要安装 pycryptodome: pip install pycryptodome")
    exit(1)


class BrowserDataExporter:
    """浏览器数据导出器"""
    
    def __init__(self):
        self.browsers = {
            "Chrome": os.path.join(os.environ['LOCALAPPDATA'], "Google", "Chrome", "User Data", "Default"),
            "Edge": os.path.join(os.environ['LOCALAPPDATA'], "Microsoft", "Edge", "User Data", "Default"),
            "Brave": os.path.join(os.environ['LOCALAPPDATA'], "BraveSoftware", "Brave-Browser", "User Data", "Default"),
        }
        self.output_dir = Path(__file__).parent / "exports"
        self.output_dir.mkdir(exist_ok=True)
    
    def get_master_key(self, browser_path):
        """获取浏览器主密钥"""
        local_state_path = os.path.join(os.path.dirname(browser_path), "Local State")
        if not os.path.exists(local_state_path):
            return None
        
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                local_state = json.load(f)
            
            encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
            master_key = CryptUnprotectData(encrypted_key[5:], None, None, None, 0)[1]
            return master_key
        except Exception as e:
            print(f"❌ 获取主密钥失败: {e}")
            return None
    
    def decrypt_payload(self, cipher_text, master_key):
        """解密数据"""
        try:
            # 新版本加密格式（v10+）
            if cipher_text[:3] == b'v10':
                iv = cipher_text[3:15]
                payload = cipher_text[15:]
                cipher = AES.new(master_key, AES.MODE_GCM, iv)
                decrypted = cipher.decrypt(payload)[:-16].decode('utf-8', errors='ignore')
                return decrypted
            # 旧版本 DPAPI 加密
            else:
                return CryptUnprotectData(cipher_text, None, None, None, 0)[1].decode('utf-8', errors='ignore')
        except Exception as e:
            return None
    
    def safe_copy_locked_file(self, source_path, dest_path, max_retries=3):
        """安全复制被锁定的文件（浏览器运行时）"""
        for attempt in range(max_retries):
            try:
                # 方法 1：直接复制（Windows 允许读取被锁定文件）
                shutil.copy2(source_path, dest_path)
                return True
            except PermissionError:
                # 方法 2：使用二进制读写（绕过某些锁）
                try:
                    with open(source_path, 'rb') as src:
                        with open(dest_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    return True
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"⚠️  文件被锁定，尝试 SQLite 在线备份...")
                        return self.sqlite_online_backup(source_path, dest_path)
                    import time
                    time.sleep(0.5)
            except Exception as e:
                print(f"❌ 复制失败: {e}")
                return False
        return False
    
    def sqlite_online_backup(self, source_db, dest_db):
        """使用 SQLite Online Backup 复制数据库"""
        try:
            # 打开源数据库（只读模式）
            source_conn = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
            # 创建目标数据库
            dest_conn = sqlite3.connect(dest_db)
            
            # 使用 backup API
            source_conn.backup(dest_conn)
            
            source_conn.close()
            dest_conn.close()
            print("✅ 使用在线备份成功")
            return True
        except Exception as e:
            print(f"❌ 在线备份失败: {e}")
            return False
    
    def export_cookies(self, browser_name, browser_path, master_key):
        """导出 Cookies（支持浏览器运行时）"""
        cookies_path = os.path.join(browser_path, "Network", "Cookies")
        if not os.path.exists(cookies_path):
            cookies_path = os.path.join(browser_path, "Cookies")
        
        if not os.path.exists(cookies_path):
            print(f"⚠️  {browser_name} Cookies 文件不存在")
            return []
        
        # 使用安全复制方法（支持浏览器运行时）
        temp_cookies = os.path.join(self.output_dir, f"temp_{browser_name}_cookies.db")
        if not self.safe_copy_locked_file(cookies_path, temp_cookies):
            print(f"❌ 无法复制 {browser_name} Cookies 文件")
            return []
        
        cookies = []
        try:
            conn = sqlite3.connect(temp_cookies)
            cursor = conn.cursor()
            cursor.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_secure, is_httponly FROM cookies")
            
            for row in cursor.fetchall():
                host, name, encrypted_value, path, expires, is_secure, is_httponly = row
                
                # 解密 cookie 值
                decrypted_value = self.decrypt_payload(encrypted_value, master_key)
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
            
            conn.close()
            print(f"✅ {browser_name} 导出 {len(cookies)} 个 Cookies")
        except Exception as e:
            print(f"❌ 导出 {browser_name} Cookies 失败: {e}")
        finally:
            # 清理临时文件
            if os.path.exists(temp_cookies):
                os.remove(temp_cookies)
        
        return cookies
    
    def export_passwords(self, browser_name, browser_path, master_key):
        """导出密码（支持浏览器运行时）"""
        login_data_path = os.path.join(browser_path, "Login Data")
        if not os.path.exists(login_data_path):
            print(f"⚠️  {browser_name} Login Data 文件不存在")
            return []
        
        # 使用安全复制方法（支持浏览器运行时）
        temp_login = os.path.join(self.output_dir, f"temp_{browser_name}_login.db")
        if not self.safe_copy_locked_file(login_data_path, temp_login):
            print(f"❌ 无法复制 {browser_name} Login Data 文件")
            return []
        
        passwords = []
        try:
            conn = sqlite3.connect(temp_login)
            cursor = conn.cursor()
            cursor.execute("SELECT origin_url, username_value, password_value FROM logins")
            
            for row in cursor.fetchall():
                url, username, encrypted_password = row
                
                # 解密密码
                decrypted_password = self.decrypt_payload(encrypted_password, master_key)
                if decrypted_password:
                    passwords.append({
                        "url": url,
                        "username": username,
                        "password": decrypted_password
                    })
            
            conn.close()
            print(f"✅ {browser_name} 导出 {len(passwords)} 个密码")
        except Exception as e:
            print(f"❌ 导出 {browser_name} 密码失败: {e}")
        finally:
            # 清理临时文件
            if os.path.exists(temp_login):
                os.remove(temp_login)
        
        return passwords
    
    def encrypt_export_data(self, data, password):
        """加密导出数据"""
        try:
            # 生成盐和密钥
            salt = get_random_bytes(32)
            key = PBKDF2(password, salt, dkLen=32, count=100000)
            
            # 加密数据
            cipher = AES.new(key, AES.MODE_GCM)
            ciphertext, tag = cipher.encrypt_and_digest(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            
            # 组合加密数据
            encrypted_data = {
                "salt": base64.b64encode(salt).decode('utf-8'),
                "nonce": base64.b64encode(cipher.nonce).decode('utf-8'),
                "tag": base64.b64encode(tag).decode('utf-8'),
                "ciphertext": base64.b64encode(ciphertext).decode('utf-8')
            }
            return encrypted_data
        except Exception as e:
            print(f"❌ 加密数据失败: {e}")
            return None
    
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
    
    def export_all(self):
        """导出所有浏览器数据"""
        print("\n" + "="*60)
        print("🔐 浏览器数据导出工具")
        print("="*60)
        print("⚠️  警告：此操作将导出敏感数据，请确保安全使用")
        print("ℹ️  提示：支持在浏览器运行时导出（无需关闭）")
        print("-"*60)
        
        all_data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "username": getpass.getuser(),
            "browsers": {}
        }
        
        for browser_name, browser_path in self.browsers.items():
            if not os.path.exists(browser_path):
                print(f"⏭️  跳过 {browser_name}（未安装）")
                continue
            
            print(f"\n📦 处理 {browser_name}...")
            
            # 获取主密钥
            master_key = self.get_master_key(browser_path)
            if not master_key:
                print(f"❌ 无法获取 {browser_name} 主密钥")
                continue
            
            # 导出数据
            cookies = self.export_cookies(browser_name, browser_path, master_key)
            passwords = self.export_passwords(browser_name, browser_path, master_key)
            
            all_data["browsers"][browser_name] = {
                "cookies": cookies,
                "passwords": passwords,
                "cookies_count": len(cookies),
                "passwords_count": len(passwords)
            }
        
        # 加密保存
        print("\n" + "-"*60)
        # 使用固定密码
        password = "cookies2026"
        print("🔒 使用预设加密密码保护导出文件")
        
        encrypted_data = self.encrypt_export_data(all_data, password)
        if not encrypted_data:
            return
        
        # 保存到文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self.output_dir / f"browser_data_{timestamp}.encrypted"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(encrypted_data, f, indent=2, ensure_ascii=False)
        
        print("\n" + "="*60)
        print("✅ 导出成功！")
        print(f"📁 文件位置: {output_file}")
        print(f"🔒 文件已加密，需要密码才能解密")
        print("\n⚠️  重要提醒：")
        print("  1. 请妥善保管此文件和密码")
        print("  2. 不要将此文件上传到公共网络")
        print("  3. 使用完毕后建议删除明文数据")
        print("="*60)


def main():
    """主函数"""
    exporter = BrowserDataExporter()
    exporter.export_all()


if __name__ == "__main__":
    main()
