from __future__ import annotations

import json
from typing import Iterable, List, Optional
from urllib import request


class D1Client:
    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.account_id = account_id
        self.database_id = database_id
        self.api_token = api_token
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}"
        )

    def _request(self, endpoint: str, payload: dict) -> dict:
        payload = {
            key: value for key, value in payload.items() if value is not None
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/{endpoint}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))

        if not data.get("success", False):
            raise RuntimeError(f"D1 query failed: {data}")

        errors = data.get("errors") or []
        if errors:
            raise RuntimeError(f"D1 query errors: {errors}")

        results = data.get("result", [])
        if not isinstance(results, list):
            raise RuntimeError(f"D1 query returned an invalid result shape: {data}")
        if not results:
            raise RuntimeError("D1 query returned no statement result")
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"D1 query result {index} has an invalid shape: {item!r}"
                )
            item_errors = item.get("errors") or item.get("error") or []
            if item.get("success") is not True or item_errors:
                raise RuntimeError(
                    f"D1 query result {index} failed: "
                    f"success={item.get('success')!r}, errors={item_errors!r}"
                )

        return data

    def query(self, sql: str, params: Optional[Iterable[object]] = None) -> List[dict]:
        data = self._request(
            "query",
            {
                "sql": sql,
                "params": list(params or []),
            },
        )
        result = data.get("result", [])
        if len(result) == 1 and isinstance(result[0], dict) and "results" in result[0]:
            return result[0].get("results", [])
        return result

    def execute(self, sql: str, params: Optional[Iterable[object]] = None) -> dict:
        return self._request(
            "query",
            {
                "sql": sql,
                "params": list(params or []),
            },
        )

    def execute_script(self, sql_script: str) -> None:
        for statement in split_sql_statements(sql_script):
            self.execute(statement)


def split_sql_statements(sql_script: str) -> List[str]:
    statements: List[str] = []
    current: List[str] = []

    for line in sql_script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []

    tail = "\n".join(current).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)

    return statements
