"""测试 pyJianYingDraft 打包的专用脚本"""
import subprocess
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def main():
    print("=== 测试 pyJianYingDraft 打包 ===")
    
    test_script = PROJECT_ROOT / "test_pyjianying_standalone.py"
    if not test_script.exists():
        print(f"错误: 测试脚本不存在: {test_script}")
        sys.exit(1)
    
    # 使用 PyInstaller 打包测试脚本
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "test_pyjianying",
        "--distpath", str(PROJECT_ROOT / "dist_test"),
        "--workpath", str(PROJECT_ROOT / "build_test"),
        "--specpath", str(PROJECT_ROOT / "scripts"),
        "--clean",
        "--console",
        "--hidden-import", "pyJianYingDraft",
    ]
    
    # 添加 pyJianYingDraft 资源文件
    try:
        import pyJianYingDraft
        from PyInstaller.utils.hooks import collect_data_files
        
        pyjianying_data = collect_data_files('pyJianYingDraft')
        for src, dst in pyjianying_data:
            cmd.extend(["--add-data", f"{src};{dst}"])
        print(f"添加资源文件: {len(pyjianying_data)} 个")
    except ImportError as e:
        print(f"警告: {e}")
    
    cmd.append(str(test_script))
    
    print(f"命令: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n打包成功！")
        
        # 运行测试
        exe_path = PROJECT_ROOT / "dist_test" / "test_pyjianying" / "test_pyjianying.exe"
        if exe_path.exists():
            print(f"\n运行测试: {exe_path}")
            subprocess.run([str(exe_path)], cwd=PROJECT_ROOT)
        else:
            print(f"错误: 可执行文件不存在: {exe_path}")
            
    except subprocess.CalledProcessError as e:
        print(f"打包失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
