#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import os
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.skill_evaluator.skill_evaluator import SkillEvaluator


def download_and_extract_zip(url: str, files_base_path: Path) -> Path:
    """Download a zip file and extract it, returning the path to the extracted folder."""
    filename = url.split("/")[-1]
    os.makedirs(files_base_path, exist_ok=True)

    zip_path = files_base_path / filename

    # Download the zip
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with zip_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("Zip downloaded: " + str(zip_path))

    # Extract — the folder name is the zip name without the suffix
    # e.g. folder_name.zip -> folder_name/
    extract_dir = files_base_path / zip_path.stem
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    logger.info("Zip extracted to: " + str(extract_dir))
    return extract_dir


async def main():
    load_dotenv()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = Path(os.getenv("OUTPUT_DIR", "outputs/evaluations"))

    zip_url = "https://bucket.agentskills.so/skills-zips/story-idea-generator_20260306074919.zip"
    files_base_path = Path(files_base_dir)

    skill_path = download_and_extract_zip(zip_url, files_base_path)

    evaluator = SkillEvaluator()
    await evaluator.create_agent()

    requirements = "Run the full pipeline"
    results = await evaluator.evaluate(skill_path, requirement=requirements, output_path=output_dir)
    logger.info(results.get("output", results))


if __name__ == "__main__":
    asyncio.run(main())