"""pytest 路径注入：让 tests/ 下测试可直接 import engine/scripts/ 同级模块
（crs_fusion 等；与 test_pool.py 文件内 sys.path.insert 同一目标，收敛到 conftest 统一提供）。
开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/scripts/
