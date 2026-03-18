from locust import HttpUser, task, between
import time

class BacktestUser(HttpUser):
    wait_time = between(0.5, 1)

    @task(10)
    def run_backtest(self):
        # Step 1: Queue the job
        res = self.client.post("/api/algotest/jobs", json={
            "index": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-06-30",
            "legs": [
                {
                    "leg_number": 1,
                    "instrument": "Option",
                    "option_type": "CE",
                    "position": "Sell",
                    "lots": 1,
                    "expiry_type": "Weekly",
                    "strike_selection": {"type": "ATM", "value": 0},
                    "entry_condition": {"type": "Market Open"},
                    "exit_condition": {"type": "At Expiry"}
                }
            ]
        })
        if res.status_code != 200:
            return

        # Step 2: Poll for result
        job_id = res.json().get("job_id")
        if not job_id:
            return

        for _ in range(30):  # poll up to 30 times
            poll = self.client.get(f"/api/algotest/jobs/{job_id}")
            if poll.json().get("status") in ["completed", "failed"]:
                break
            time.sleep(1)

    @task(1)
    def health_check(self):
        self.client.get("/health")
