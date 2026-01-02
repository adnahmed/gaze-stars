#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# @Author  :   Arthals
# @File    :   Stargazer.py
# @Time    :   2025/01/22 16:16:16
# @Contact :   zhuozhiyongde@126.com
# @Software:   Visual Studio Code

import json
import os
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class Stargazer:
    def __init__(self):
        self.username = os.getenv("GITHUB_USERNAME")
        self.token = os.getenv("GITHUB_TOKEN")
        self.template = os.getenv("TEMPLATE_PATH", "template/template.md")
        self.output = os.getenv("OUTPUT_PATH", "README.md")
        self.sort_by = os.getenv("SORT_BY", "stars")
        self.star_lists = []
        self.star_list_repos = {}
        self.data = {}
        # data file (JSON Lines) for streaming large results
        self.data_file = os.getenv("DATA_FILE", "data.jsonl")
        # create a resilient HTTP session
        self.session = self._make_session()
        # whether to write consolidated JSON (disabled for large datasets)
        self.write_consolidated = False

    def _make_session(self):
        session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("HEAD", "GET", "OPTIONS"),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get_all_starred(self):
        url = f"https://api.github.com/users/{self.username}/starred?per_page=100"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Stargazer",
        }
        # Stream results to a JSON Lines file to avoid keeping everything in memory
        with open(self.data_file, "w", encoding="utf-8") as out_f:
            while url:
                response = self.session.get(url, headers=headers, timeout=10)
                # handle rate limiting
                self._handle_rate_limit(response)
                response.raise_for_status()
                page_items = response.json()
                for repo in page_items:
                    entry = {
                        "full_name": repo["full_name"],
                        "html_url": repo["html_url"],
                        "description": repo["description"] or "",
                        "listed": False,
                        "stars": repo["stargazers_count"],
                    }
                    out_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                url = response.links.get("next", {}).get("url")

        # optionally load data into memory for generation (disabled for huge datasets)
        if self.write_consolidated:
            self.load_data_from_jsonl()
        else:
            # keep minimal in-memory index empty until explicitly loaded
            self.data = {}
        return None

    def load_data_from_jsonl(self):
        """Load data from the JSON Lines file into self.data (full_name -> metadata)."""
        data = {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        full = obj.get("full_name")
                        if full:
                            data[full] = {
                                "html_url": obj.get("html_url", ""),
                                "description": obj.get("description", ""),
                                "listed": obj.get("listed", False),
                                "stars": obj.get("stars", 0),
                            }
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            self.data = {}
            return
        self.data = data

    def _handle_rate_limit(self, response):
        # If GitHub signals rate limit reached, sleep until reset
        headers = response.headers
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is not None and reset is not None:
            try:
                if int(remaining) == 0:
                    reset_ts = int(reset)
                    sleep_for = max(0, reset_ts - int(time.time()) + 5)
                    time.sleep(sleep_for)
            except ValueError:
                pass

    def get_lists(self):
        url = f"https://github.com/{self.username}?tab=stars"
        response = self.session.get(url, timeout=10)
        pattern = f'href="/stars/{self.username}/lists/(\S+)".*?<h3 class="f4 text-bold no-wrap mr-3">(.*?)</h3>'
        match = re.findall(pattern, response.text, re.DOTALL)
        self.star_lists = [(url, name.strip()) for url, name in match]
        return self.star_lists

    def get_list_repos(self, list_name):
        page_url_template = (
            "https://github.com/stars/{username}/lists/{list_name}?page={page}"
        )
        page = 1
        # ensure key exists so we always return a list (avoid KeyError if page has no matches)
        if list_name not in self.star_list_repos:
            self.star_list_repos[list_name] = []
        while True:
            current_url = page_url_template.format(
                username=self.username, list_name=list_name, page=page
            )
            response = self.session.get(current_url, timeout=10)
            pattern = r'<h3>\s*<a href="[^"]*">\s*<span class="text-normal">(\S+) / </span>(\S+)\s+</a>\s*</h3>'
            match = re.findall(pattern, response.text)
            if not match:
                break
            if list_name not in self.star_list_repos:
                self.star_list_repos[list_name] = []
            self.star_list_repos[list_name].extend(match)
            page += 1
        return self.star_list_repos.get(list_name, [])

    def get_all_repos(self):
        for list_url, _ in self.star_lists:
            self.get_list_repos(list_url)
        return self.star_list_repos

    def generate_readme(self):
        sections = [name for _, name in self.star_lists]
        sections.append("Uncategorized Repositories")
        text = ""

        # Generate category tables
        for list_url, list_name in self.star_lists:
            # 获取当前分类仓库并按stars降序
            repos = [
                (f"{user}/{repo}", self.data[f"{user}/{repo}"])
                for user, repo in self.star_list_repos.get(list_url, [])
                if f"{user}/{repo}" in self.data
            ]
            # sorted_repos = sorted(repos, key=lambda x: x[1]["stars"], reverse=True)
            if self.sort_by == "stars":
                sorted_repos = sorted(repos, key=lambda x: x[1]["stars"], reverse=True)
            else:
                # reverse repo
                sorted_repos = repos[::-1]
            # 生成表格内容
            text += f"## {list_name}\n\n"
            text += "| Repository | Description | Stars |\n"
            text += "|----------|------|-------|\n"
            for key, repo in sorted_repos:
                repo["listed"] = True
                desc = repo["description"].replace("|", "\\|")
                text += f"| [{key}](https://github.com/{key}) | {desc} | ⭐{repo['stars']} |\n"
            text += "\n"

        # 生成未分类表格
        if self.sort_by == "stars":
            unlisted = sorted(
                [key for key in self.data if not self.data[key]["listed"]],
                key=lambda x: self.data[x]["stars"],
                reverse=True,
            )
        else:
            unlisted = [key for key in self.data if not self.data[key]["listed"]]
            unlisted = unlisted[::-1]

        text += "## Uncategorized Repositories\n\n"
        text += "| Repository | Description | Stars |\n"
        text += "|----------|------|-------|\n"

        if not unlisted:
            text += "| *All repositories are categorized* | | |\n"
        else:
            for k in unlisted:
                desc = self.data[k]["description"].replace("|", "\\|")
                text += f"| [{k}](https://github.com/{k}) | {desc} | ⭐{self.data[k]['stars']} |\n"

        text += "\n"

        toc = self.build_toc(sections)
        if toc:
            text = f"{toc}{text}"

        # Generate full README
        with open(self.template, "r", encoding="utf-8") as f:
            template = f.read()

        with open(self.output, "w", encoding="utf-8") as f:
            f.write(template.replace("[[GENERATE HERE]]", text.strip()))

    def build_toc(self, sections):
        cleaned_sections = [section for section in sections if section]
        if not cleaned_sections:
            return ""
        anchors = {}
        toc_lines = ["## TOC", ""]
        for section in cleaned_sections:
            slug = self.slugify(section)
            count = anchors.get(slug, 0)
            unique_slug = slug if count == 0 else f"{slug}-{count}"
            anchors[slug] = count + 1
            toc_lines.append(f"- [{section}](#{unique_slug})")
        toc_lines.append("")
        return "\n".join(toc_lines)

    def slugify(self, text):
        slug = text.strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        return slug or "section"


if __name__ == "__main__":
    stargazer = Stargazer()
    stargazer.get_all_starred()  # fetch all starred repositories (streamed to data.jsonl)
    stargazer.get_lists()  # fetch star lists (categories)
    stargazer.get_all_repos()  # fetch repositories in each category
    # load data into memory from JSONL for README generation
    stargazer.load_data_from_jsonl()
    stargazer.generate_readme()  # generate README
