from setuptools import find_namespace_packages, setup


setup(
    name="cli-anything-microsoft-office",
    version="0.2.0",
    description="Agent-native CLI harness for installed Microsoft Word, Excel, and PowerPoint",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
    package_data={
        "cli_anything.microsoft_office": ["skills/*.md", "skills/references/*.md"],
        "cli_anything.microsoft_office.utils": ["*.ps1"],
    },
    python_requires=">=3.10",
    install_requires=["click>=8.1", "prompt_toolkit>=3.0"],
    entry_points={
        "console_scripts": [
            "cli-anything-microsoft-office=cli_anything.microsoft_office.microsoft_office_cli:main",
        ],
    },
)
