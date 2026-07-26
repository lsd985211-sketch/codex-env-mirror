from setuptools import find_namespace_packages, setup


setup(
    name="cli-anything-weixin",
    version="0.1.0",
    description="Agent-native CLI harness for Windows Weixin desktop GUI operations",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
    package_data={"cli_anything.weixin": ["skills/*.md"]},
    install_requires=["click", "Pillow", "pyautogui", "pyperclip", "pywinauto"],
    entry_points={
        "console_scripts": [
            "cli-anything-weixin=cli_anything.weixin.weixin_cli:cli",
        ],
    },
)
