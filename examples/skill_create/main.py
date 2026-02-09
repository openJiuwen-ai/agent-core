#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import os
from pathlib import Path
import requests

from dotenv import load_dotenv
import markitdown

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.skill_creator.skill_creator import SkillCreator


def download_file_as_markdown(url, files_base_dir):
    filename = url.split("/")[-1]
    os.makedirs(files_base_dir, exist_ok=True)

    pdf_path = files_base_dir / filename
    md_path = pdf_path.with_suffix(".md")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    with pdf_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("PDF downloaded: " + str(pdf_path))

    md = markitdown.MarkItDown()
    result = md.convert(str(pdf_path))

    with md_path.open("w", encoding="utf-8") as f:
        f.write(result.text_content)

    logger.info("Markdown saved: " + str(md_path))
    return md_path


async def main():
    # Load environment
    load_dotenv()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = os.getenv("OUTPUT_DIR", "")

    skill_creator = SkillCreator()
    await skill_creator.create_agent()

    # Download a manual to base the skill off of.
    url = "http://viewer.media.bitpipe.com/1253203751_753/1284482743_310/11_Best_Practices_for_Peer_Code_Review.pdf"
    files_base_path = Path(files_base_dir)
    md_path = download_file_as_markdown(url, files_base_path)
    
    # Generate skill
    query = f"Create a skill based on the file {md_path}."
    res = await skill_creator.generate(query, output_dir)
    logger.info(res.get("output", res))

if __name__ == "__main__":
    asyncio.run(main())
