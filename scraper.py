    def _try_fill_search_form(self, page, name: str) -> bool:
        try:
            # Prefer the exact public form layout used by the CyberBackgroundChecks name search page.
            full_name = (name or "").strip()
            if not full_name:
                return False

            parts = [p.strip() for p in full_name.split() if p.strip()]
            first_name = parts[0] if len(parts) >= 1 else ""
            middle_name = " ".join(parts[1:-1]) if len(parts) > 2 else ""
            last_name = parts[-1] if len(parts) >= 2 else ""

            exact_selectors = [
                ("first", "input[placeholder*='First Name' i], input[aria-label*='First Name' i], input[name*='first' i], input[id*='first' i]"),
                ("middle", "input[placeholder*='MI' i], input[aria-label*='MI' i], input[name*='middle' i], input[id*='middle' i], input[name*='mi' i], input[id*='mi' i]"),
                ("last", "input[placeholder*='Last Name' i], input[aria-label*='Last Name' i], input[name*='last' i], input[id*='last' i]"),
                ("city", "input[placeholder*='City & State' i], input[aria-label*='City & State' i], input[name*='city' i], input[id*='city' i], input[name*='location' i], input[id*='location' i]"),
            ]

            filled = False
            for field_key, selector in exact_selectors:
                locator = page.locator(selector)
                if locator.count() == 0:
                    continue

                value = {
                    "first": first_name,
                    "middle": middle_name,
                    "last": last_name,
                    "city": "",
                }.get(field_key, "")

                if value:
                    locator.first.fill(value)
                    filled = True

            # Fall back to generic form detection if the exact selectors are not present.
            if not filled:
                forms = page.locator("form")
                if forms.count() == 0:
                    return False

                for form_idx in range(forms.count()):
                    form = forms.nth(form_idx)
                    inputs = form.locator("input, textarea, select")
                    if inputs.count() == 0:
                        continue

                    for idx in range(inputs.count()):
                        locator = inputs.nth(idx)
                        field_name = (locator.get_attribute("name") or "").lower()
                        field_placeholder = (locator.get_attribute("placeholder") or "").lower()
                        field_id = (locator.get_attribute("id") or "").lower()
                        field_type = (locator.get_attribute("type") or "").lower()
                        if field_type in {"hidden"}:
                            continue

                        if any(t in field_name for t in ("first", "fname", "firstname")) and first_name:
                            locator.fill(first_name)
                            filled = True
                        elif any(t in field_name for t in ("middle", "mi")) and middle_name:
                            locator.fill(middle_name)
                            filled = True
                        elif any(t in field_name for t in ("last", "lname", "lastname")) and last_name:
                            locator.fill(last_name)
                            filled = True
                        elif any(t in field_name for t in ("city", "state", "location")):
                            locator.fill("")
                            filled = True
                        elif any(t in field_placeholder for t in ("first", "last", "name")) and not locator.input_value():
                            locator.fill(first_name if "first" in field_placeholder else last_name)
                            filled = True

            # Submit using a likely public button or the first submit control.
            submit_candidates = [
                page.locator("button:has-text('Search')"),
                page.locator("button[type='submit']"),
                page.locator("input[type='submit']"),
                page.locator("button[aria-label*='Search' i]"),
                page.locator("button:has-text('Submit')"),
            ]

            clicked = False
            for candidate in submit_candidates:
                if candidate.count() == 0:
                    continue
                try:
                    candidate.first.click(timeout=self.timeout)
                    clicked = True
                    break
                except Exception:
                    continue

            if not clicked:
                try:
                    page.keyboard.press("Enter")
                    clicked = True
                except Exception:
                    pass

            if clicked:
                self._wait_ready(page)
                return True

            return filled

        except Exception:
            logger.debug("搜索表单填充失败", exc_info=True)
            return False
