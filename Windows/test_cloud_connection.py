#!/usr/bin/env python3
"""
Windows 平台雲端端點連接測試腳本
用於測試與雲端 AI 模型訓練及分析服務的連接
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
import urllib3

# 禁用 SSL 警告（僅用於測試）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CloudConnectionTester:
    def __init__(self):
        self.base_url = "https://innate-plexus-461807-t3.de.r.appspot.com"
        self.test_results = []
        self.session = requests.Session()
        
        # 設定請求標頭
        self.session.headers.update({
            'User-Agent': 'WoundMeasurement-Windows-Client/1.0',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
    def log_test(self, test_name, success, message, response_time=None):
        """記錄測試結果"""
        result = {
            'test_name': test_name,
            'success': success,
            'message': message,
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time
        }
        self.test_results.append(result)
        
        status = "✓" if success else "✗"
        print(f"{status} {test_name}: {message}")
        if response_time:
            print(f"   回應時間: {response_time:.3f}秒")
    
    def test_basic_connectivity(self):
        """測試基本連接性"""
        try:
            start_time = time.time()
            response = self.session.get(f"{self.base_url}/", timeout=10)
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                self.log_test("基本連接測試", True, f"連接成功 (狀態碼: {response.status_code})", response_time)
                return True
            else:
                self.log_test("基本連接測試", False, f"連接失敗 (狀態碼: {response.status_code})", response_time)
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_test("基本連接測試", False, f"連接錯誤: {str(e)}")
            return False
    
    def test_health_endpoint(self):
        """測試健康檢查端點"""
        try:
            start_time = time.time()
            response = self.session.get(f"{self.base_url}/health", timeout=10)
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                try:
                    health_data = response.json()
                    self.log_test("健康檢查端點", True, f"服務健康: {health_data}", response_time)
                    return True
                except json.JSONDecodeError:
                    self.log_test("健康檢查端點", False, "回應格式錯誤 (非 JSON)")
                    return False
            else:
                self.log_test("健康檢查端點", False, f"健康檢查失敗 (狀態碼: {response.status_code})", response_time)
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_test("健康檢查端點", False, f"請求錯誤: {str(e)}")
            return False
    
    def test_doctor_authentication(self):
        """測試醫師認證端點"""
        try:
            auth_data = {
                "doctor_id": "D001",
                "password": "REMOVED_USE_BACKEND_AUTH",
                "hospital": "台大醫院"
            }
            
            start_time = time.time()
            response = self.session.post(
                f"{self.base_url}/auth/login",
                json=auth_data,
                timeout=10
            )
            response_time = time.time() - start_time
            
            if response.status_code in [200, 401, 422]:  # 正常回應（包括認證失敗）
                self.log_test("醫師認證端點", True, f"端點可達 (狀態碼: {response.status_code})", response_time)
                return True
            else:
                self.log_test("醫師認證端點", False, f"端點錯誤 (狀態碼: {response.status_code})", response_time)
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_test("醫師認證端點", False, f"請求錯誤: {str(e)}")
            return False
    
    def test_annotation_upload(self):
        """測試標註資料上傳端點"""
        try:
            # 模擬標註資料
            annotation_data = {
                "doctor_id": "D001",
                "patient_id": "test_patient_001",
                "image_filename": "test_wound.jpg",
                "bjwat_scores": {
                    "size": 3,
                    "depth": 2,
                    "edges": 2,
                    "undermining": 1,
                    "necrotic_tissue": 1,
                    "exudate": 2,
                    "granulation": 2,
                    "epithelialization": 1
                },
                "revpwat_scores": {
                    "surface_area": 3,
                    "depth": 2,
                    "edges": 2,
                    "undermining": 1,
                    "necrotic_tissue": 1,
                    "exudate": 2
                }
            }
            
            start_time = time.time()
            response = self.session.post(
                f"{self.base_url}/upload/annotation",
                json=annotation_data,
                timeout=15
            )
            response_time = time.time() - start_time
            
            if response.status_code in [200, 201, 422]:  # 正常回應
                self.log_test("標註上傳端點", True, f"端點可達 (狀態碼: {response.status_code})", response_time)
                return True
            else:
                self.log_test("標註上傳端點", False, f"端點錯誤 (狀態碼: {response.status_code})", response_time)
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_test("標註上傳端點", False, f"請求錯誤: {str(e)}")
            return False
    
    def test_image_upload(self):
        """測試影像上傳端點"""
        try:
            # 簡化版本：只測試 JSON 格式的影像上傳
            image_data = {
                'doctor_id': 'D001',
                'patient_id': 'test_patient_001',
                'annotation_id': 'test_annotation_001'
            }
            
            start_time = time.time()
            response = self.session.post(
                f"{self.base_url}/upload/image",
                json=image_data,
                timeout=30
            )
            response_time = time.time() - start_time
            
            if response.status_code in [200, 201, 422]:  # 正常回應
                self.log_test("影像上傳端點", True, f"端點可達 (狀態碼: {response.status_code})", response_time)
                return True
            else:
                self.log_test("影像上傳端點", False, f"端點錯誤 (狀態碼: {response.status_code})", response_time)
                return False
                
        except requests.exceptions.RequestException as e:
            self.log_test("影像上傳端點", False, f"請求錯誤: {str(e)}")
            return False
        except Exception as e:
            self.log_test("影像上傳端點", False, f"測試錯誤: {str(e)}")
            return False
    
    def create_test_image(self, image_path):
        """創建測試影像"""
        try:
            import cv2
            import numpy as np
            
            # 創建簡單的測試影像
            image = np.ones((480, 640, 3), dtype=np.uint8) * 255
            
            # 添加模擬傷口
            cv2.circle(image, (320, 240), 80, (100, 50, 50), -1)
            cv2.circle(image, (320, 240), 60, (150, 100, 100), -1)
            
            # 保存影像
            cv2.imwrite(str(image_path), image)
            print(f"✓ 創建測試影像: {image_path}")
            
        except ImportError:
            # 如果沒有 OpenCV，創建一個簡單的文字檔案
            with open(image_path, 'w') as f:
                f.write("Test image content")
            print(f"✓ 創建測試檔案: {image_path}")
    
    def test_network_performance(self):
        """測試網路效能"""
        try:
            times = []
            for i in range(5):
                start_time = time.time()
                response = self.session.get(f"{self.base_url}/health", timeout=10)
                response_time = time.time() - start_time
                times.append(response_time)
                
                if response.status_code != 200:
                    self.log_test("網路效能測試", False, f"第 {i+1} 次請求失敗")
                    return False
            
            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)
            
            self.log_test("網路效能測試", True, 
                         f"平均回應時間: {avg_time:.3f}秒 (最小: {min_time:.3f}秒, 最大: {max_time:.3f}秒)")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log_test("網路效能測試", False, f"效能測試失敗: {str(e)}")
            return False
    
    def generate_report(self):
        """生成測試報告"""
        report_path = Path("cloud_connection_test_report.json")
        
        # 計算統計資訊
        total_tests = len(self.test_results)
        successful_tests = sum(1 for result in self.test_results if result['success'])
        success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
        
        # 計算平均回應時間
        response_times = [r['response_time'] for r in self.test_results if r['response_time'] is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        report = {
            'test_summary': {
                'total_tests': total_tests,
                'successful_tests': successful_tests,
                'success_rate': success_rate,
                'average_response_time': avg_response_time,
                'test_timestamp': datetime.now().isoformat(),
                'base_url': self.base_url
            },
            'test_results': self.test_results
        }
        
        # 保存報告
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"\n📊 測試報告已保存至: {report_path}")
        print(f"📈 成功率: {success_rate:.1f}% ({successful_tests}/{total_tests})")
        print(f"⏱️  平均回應時間: {avg_response_time:.3f}秒")
        
        return report
    
    def run_all_tests(self):
        """執行所有測試"""
        print("🚀 開始 Windows 平台雲端端點連接測試")
        print(f"📍 目標服務: {self.base_url}")
        print("=" * 60)
        
        tests = [
            self.test_basic_connectivity,
            self.test_health_endpoint,
            self.test_doctor_authentication,
            self.test_annotation_upload,
            self.test_image_upload,
            self.test_network_performance
        ]
        
        for test in tests:
            try:
                test()
                time.sleep(1)  # 避免過於頻繁的請求
            except Exception as e:
                self.log_test(test.__name__, False, f"測試執行錯誤: {str(e)}")
        
        print("=" * 60)
        print("🏁 測試完成")
        
        # 生成報告
        self.generate_report()

def main():
    """主函數"""
    print("🔧 Windows 平台雲端端點連接測試工具")
    print("=" * 60)
    
    # 檢查依賴
    try:
        import requests
        print("✓ requests 套件已安裝")
    except ImportError:
        print("✗ 缺少 requests 套件")
        print("請執行: pip install requests")
        return
    
    # 執行測試
    tester = CloudConnectionTester()
    tester.run_all_tests()

if __name__ == "__main__":
    main() 