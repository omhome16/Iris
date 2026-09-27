"""Live smoke: onboarding wizard → real chat turn → memory write path."""

from fastapi.testclient import TestClient

from iris_ai.api import app


def main() -> None:
    with TestClient(app) as c:
        answers = ["Hi", "Omar", "warm and curious", "short and direct", "Asia/Kolkata", "4"]
        for i, a in enumerate(answers):
            r = c.post("/chat", json={"message": a, "session_id": "test1"})
            print(f"  step {i}: {r.json()['reply']}")
        print("onboarded:", c.get("/onboarding").json()["onboarded"])

        r = c.post(
            "/chat",
            json={
                "message": "I just started a new job as an ML engineer at Google. Remember that.",
                "session_id": "test1",
            },
        )
        print("chat:", r.json()["reply"])

        print("health:", c.get("/health").json())
        print("staging files:", [p.name for p in (c.app.state.runtime.files.staging_dir()).glob("staging-*.jsonl")])


if __name__ == "__main__":
    main()
