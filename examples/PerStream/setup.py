from setuptools import setup, find_packages

setup(
    name="perstream",
    version="0.1.0",
    packages=find_packages(where="."),
    package_dir={"": "."},
    python_requires=">=3.8",
)
