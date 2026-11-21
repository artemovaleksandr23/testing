from locust import HttpUser, task, between
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class PublicAPIUser(HttpUser):
    host = "https://jsonplaceholder.typicode.com"
    wait_time = between(1, 3)

    @task(2)
    def get_posts(self):
        with self.client.get("/posts", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"JSONPlaceholder: HTTP {response.status_code}")

    @task(1)
    def get_weather(self):
        with self.client.get("https://wttr.in/Novosibirsk?format=j1", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"wttr.in: HTTP {response.status_code}")
