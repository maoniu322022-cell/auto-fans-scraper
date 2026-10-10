    def _collect_cards_current_page(self, page) -> list[dict]:
        selectors = [
            "a[href*='/people/' i]",
            "a[href*='/person/' i]",
            "a[href*='/detail/' i]",
            "a[href*='/profile/' i]",
            "a[href*='detail' i]",
            "a[href*='profile' i]",
            "a[href*='person' i]",
            "a[href*='people' i]",
            "a[href*='name' i]",
            "a:has-text('View Details')",
            "a:has-text('View All Info')",
            "a:has-text('Full Details')",
            "a:has-text('More Info')",
        ]
        selector = ", ".join(dict.fromkeys(selectors))

        try:
            raw_cards = page.locator(selector).evaluate_all(
                r"""
                links => links.map(link => {
                    let node = link;
                    let cardText = "";

                    while (node && node !== document.body) {
                        const text = node.innerText || "";

                        if (/Approximate\s+Age\s*:/i.test(text) || /\bAge\b/i.test(text) || /\b\d{2}\s*years?\s*old\b/i.test(text) || /Deceased/i.test(text) || /Lives at/i.test(text)) {
                            cardText = text;
                            break;
                        }

                        node = node.parentElement;
                    }

                    return {
                        href: link.href || "",
                        text: cardText || (link.innerText || "")
                    };
                })
                """
            )

        except Exception:
            logger.exception("读取列表候选项失败")
            return []

        candidates = []
        seen_urls = set()

        for item in raw_cards:
            detail_url = (item.get("href") or "").strip()
            if not detail_url:
                continue

            if detail_url.lower().startswith("javascript:"):
                continue

            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            text = item.get("text") or ""
            age = self._extract_age(text)

            # Important: real CyberBackgroundChecks result pages often contain no numeric age,
            # or show 'Age: Deceased'. We should still accept them as valid candidates,
            # and only skip explicit out-of-range numeric ages.
            if age is not None and not self.min_age <= age <= self.max_age:
                continue

            # Keep direct /people/... result rows even when they do not expose age in the DOM.
            if "/people/" in detail_url.lower() or "/person/" in detail_url.lower() or "/detail/" in detail_url.lower() or "/profile/" in detail_url.lower():
                candidates.append({
                    "age": age,
                    "detail_url": detail_url,
                })
                continue

            candidates.append({
                "age": age,
                "detail_url": detail_url,
            })

            if (
                self.max_candidates_per_query is not None
                and len(candidates) >= self.max_candidates_per_query
            ):
                break

        return candidates
