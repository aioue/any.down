#!/usr/bin/env python3
"""
Setup script for the Any.do API client.

This script will:
1. Create a virtual environment
2. Install dependencies
3. Run tests to verify everything works
4. Provide usage instructions
"""

import os
import sys
import subprocess
import platform

def run_command(cmd, description):
    """Run a command and handle errors."""
    print(f"📋 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed:")
        print(f"   Command: {cmd}")
        print(f"   Error: {e.stderr}")
        return False

def main():
    """Main setup function."""
    print("🚀 Any.do API Client Setup")
    print("=" * 50)
    
    # Check Python version
    if sys.version_info < (3, 7):
        print("❌ Python 3.7 or higher is required")
        sys.exit(1)
    
    print(f"✅ Python {sys.version.split()[0]} detected")
    
    # Create virtual environment
    if not os.path.exists("venv"):
        if not run_command("python3 -m venv venv", "Creating virtual environment"):
            sys.exit(1)
    else:
        print("✅ Virtual environment already exists")
    
    # Determine activation command based on OS
    if platform.system() == "Windows":
        activate_cmd = "venv\\Scripts\\activate"
        pip_cmd = "venv\\Scripts\\pip"
        python_cmd = "venv\\Scripts\\python"
    else:
        activate_cmd = "source venv/bin/activate"
        pip_cmd = "venv/bin/pip"
        python_cmd = "venv/bin/python"
    
    # Install dependencies
    install_cmd = f"{pip_cmd} install -r requirements.txt"
    if not run_command(install_cmd, "Installing dependencies"):
        sys.exit(1)
    
    # Run tests
    test_cmd = f"{python_cmd} run_tests.py"
    if not run_command(test_cmd, "Running tests"):
        print("⚠️  Some tests failed, but the setup is complete")
    
    print("\n🎉 Setup completed successfully!")
    print("\n📖 Usage Instructions:")
    print("=" * 50)
    
    if platform.system() == "Windows":
        print("1. Activate the virtual environment:")
        print("   venv\\Scripts\\activate")
    else:
        print("1. Activate the virtual environment:")
        print("   source venv/bin/activate")
    
    print("\n2. Run the script to fetch your tasks:")
    print("   python anydown.py")
    
    print("\n3. Or use the library in your own scripts:")
    print("   from anydo_client import AnyDoClient")
    
    print("\n4. Run tests anytime:")
    print("   python run_tests.py")
    
    print("\n5. Deactivate the virtual environment when done:")
    print("   deactivate")
    
    print("\n🔧 Development:")
    print("- Edit anydo_client.py to modify the core library")
    print("- Edit anydown.py to modify the main application")
    print("- Add tests to test_*.py files")
    print("- Use debug_login.py for troubleshooting authentication")
    
    print("\n📚 Documentation:")
    print("- See README.md for detailed usage instructions")
    print("- Check the source code for API documentation")

if __name__ == "__main__":
    main() 
