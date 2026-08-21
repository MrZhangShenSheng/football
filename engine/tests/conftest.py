"""pytest 配置：把 engine/scripts 加进 import 路径。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
