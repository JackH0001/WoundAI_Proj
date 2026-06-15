#!/usr/bin/env python3
"""
載入本地影像功能測試腳本
用於驗證影像載入和處理功能
"""

import os
import sys
import subprocess
import time
from pathlib import Path

def check_dependencies():
    """檢查必要的依賴套件"""
    try:
        import cv2
        import numpy as np
        from PIL import Image
        print("✓ 所有依賴套件已安裝")
        return True
    except ImportError as e:
        print(f"✗ 缺少依賴套件: {e}")
        print("請執行: pip install opencv-python numpy pillow")
        return False

def create_test_images():
    """創建測試影像"""
    try:
        import cv2
        import numpy as np
        
        # 創建測試目錄
        test_dir = Path("test_images")
        test_dir.mkdir(exist_ok=True)
        
        # 創建不同類型的測試影像
        test_cases = [
            ("simple_wound.jpg", 640, 480),
            ("high_res_wound.jpg", 1920, 1080),
            ("small_wound.jpg", 320, 240),
            ("test_wound.png", 800, 600),
            ("test_wound.bmp", 640, 480)
        ]
        
        for filename, width, height in test_cases:
            filepath = test_dir / filename
            
            # 創建模擬傷口影像
            image = np.ones((height, width, 3), dtype=np.uint8) * 255
            
            # 添加皮膚紋理
            noise = np.random.normal(0, 15, (height, width, 3)).astype(np.uint8)
            image = cv2.add(image, noise)
            
            # 創建橢圓形傷口
            center_x, center_y = width // 2, height // 2
            wound_width, wound_height = width // 4, height // 4
            
            # 傷口外圍
            cv2.ellipse(image, (center_x, center_y), (wound_width + 20, wound_height + 15), 
                        0, 0, 360, (200, 100, 100), -1)
            
            # 傷口內部
            cv2.ellipse(image, (center_x, center_y), (wound_width, wound_height), 
                        0, 0, 360, (100, 50, 50), -1)
            
            # 添加測量標記
            cv2.line(image, (50, 50), (150, 50), (0, 0, 0), 2)
            cv2.putText(image, "10mm", (80, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
            
            # 保存影像
            cv2.imwrite(str(filepath), image)
            print(f"✓ 創建測試影像: {filepath}")
        
        return True
        
    except Exception as e:
        print(f"✗ 創建測試影像失敗: {e}")
        return False

def test_image_formats():
    """測試不同影像格式的載入"""
    try:
        import cv2
        from PIL import Image
        
        test_dir = Path("test_images")
        if not test_dir.exists():
            print("✗ 測試影像目錄不存在")
            return False
        
        supported_formats = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']
        
        for filepath in test_dir.glob("*"):
            if filepath.suffix.lower() in supported_formats:
                try:
                    # 測試 OpenCV 載入
                    cv_image = cv2.imread(str(filepath))
                    if cv_image is not None:
                        print(f"✓ OpenCV 載入成功: {filepath.name}")
                    else:
                        print(f"✗ OpenCV 載入失敗: {filepath.name}")
                    
                    # 測試 PIL 載入
                    pil_image = Image.open(filepath)
                    print(f"✓ PIL 載入成功: {filepath.name} ({pil_image.size})")
                    
                except Exception as e:
                    print(f"✗ 載入失敗 {filepath.name}: {e}")
        
        return True
        
    except Exception as e:
        print(f"✗ 測試影像格式失敗: {e}")
        return False

def test_image_quality():
    """測試影像品質評估"""
    try:
        import cv2
        import numpy as np
        
        test_dir = Path("test_images")
        if not test_dir.exists():
            return False
        
        for filepath in test_dir.glob("*.jpg"):
            # 載入影像
            image = cv2.imread(str(filepath))
            if image is None:
                continue
            
            # 計算品質指標
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            
            # 亮度
            brightness = np.mean(gray)
            
            # 對比度
            contrast = np.std(gray)
            
            # 清晰度 (Laplacian variance)
            laplacian = cv2.Laplacian(gray, cv2.CV_64F)
            sharpness = laplacian.var()
            
            print(f"影像: {filepath.name}")
            print(f"  亮度: {brightness:.1f}")
            print(f"  對比度: {contrast:.1f}")
            print(f"  清晰度: {sharpness:.1f}")
            
            # 品質評估
            quality_score = 0
            if 50 <= brightness <= 200:
                quality_score += 30
            if contrast > 20:
                quality_score += 30
            if sharpness > 100:
                quality_score += 40
            
            print(f"  品質分數: {quality_score}/100")
            print()
        
        return True
        
    except Exception as e:
        print(f"✗ 測試影像品質失敗: {e}")
        return False

def run_tests():
    """執行所有測試"""
    print("載入本地影像功能測試")
    print("=" * 40)
    
    # 檢查依賴
    if not check_dependencies():
        return False
    
    # 創建測試影像
    print("\n1. 創建測試影像...")
    if not create_test_images():
        return False
    
    # 測試影像格式
    print("\n2. 測試影像格式支援...")
    if not test_image_formats():
        return False
    
    # 測試影像品質
    print("\n3. 測試影像品質評估...")
    if not test_image_quality():
        return False
    
    print("\n✓ 所有測試通過！")
    print("\n測試影像已創建在 'test_images' 目錄中")
    print("您可以使用這些影像來測試 WPF 應用程式的載入本地影像功能")
    
    return True

def cleanup():
    """清理測試檔案"""
    try:
        import shutil
        test_dir = Path("test_images")
        if test_dir.exists():
            shutil.rmtree(test_dir)
            print("✓ 清理測試檔案完成")
    except Exception as e:
        print(f"✗ 清理失敗: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cleanup":
        cleanup()
    else:
        success = run_tests()
        if not success:
            sys.exit(1) 