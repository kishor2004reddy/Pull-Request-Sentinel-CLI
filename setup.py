from setuptools import setup, find_packages

setup(
    name="pr-sentinel",
    version="0.1.0",
    description="Local PR review tool that orchestrates Claude Code CLI agents over git diffs.",
    author="PR Sentinel",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={
        "pr_sentinel": ["prompts/*.md"],
    },
    install_requires=[
        "click>=8.1",
    ],
    entry_points={
        "console_scripts": [
            "pr-sentinel=pr_sentinel.cli:main",
        ],
    },
    python_requires=">=3.11",
)
