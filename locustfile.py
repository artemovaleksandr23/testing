import os
import logging
from locust import HttpUser, task, between
import urllib3

# Настройка логгера для вывода PowerState
logger = logging.getLogger(__name__)

# Отключаем предупреждения о самоподписанном сертификате
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USERNAME = "root"
# Строгое требование переменной окружения
PASSWORD = os.environ["BMC_PASSWORD"]

class OpenBMCUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Создание Redfish-сессии и строгая проверка заголовков"""
        self.token = None
        self.session_url = None

        with self.client.post(
            "/redfish/v1/SessionService/Sessions",
            json={"UserName": USERNAME, "Password": PASSWORD},
            verify=False,
            catch_response=True
        ) as response:
            if response.status_code in [200, 201]:
                token = response.headers.get("X-Auth-Token")
                location = response.headers.get("Location")
                if token and location:
                    self.token = token
                    self.session_url = location
                    response.success()
                else:
                    response.failure("В ответе отсутствует X-Auth-Token или Location")
            else:
                response.failure(f"Не удалось создать сессию: HTTP {response.status_code}")

    def on_stop(self):
        """Корректное удаление сессии"""
        if self.token and self.session_url:
            headers = {"X-Auth-Token": self.token}
            self.client.delete(self.session_url, headers=headers, verify=False)

    @task(3)
    def get_system_info(self):
        """Чтение информации о системе с проверкой PowerState"""
        if not self.token:
            return
        headers = {"X-Auth-Token": self.token}
        with self.client.get(
            "/redfish/v1/Systems/system",
            headers=headers,
            verify=False,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    power_state = data.get("PowerState")
                    # Логирование фактического значения для отчёта
                    logger.info(f"Фактическое значение PowerState: {power_state}")

                    if power_state in ["On", "Off", "PoweringOn", "PoweringOff"]:
                        response.success()
                    else:
                        response.failure(f"Некорректный PowerState: {power_state}")
                except ValueError:
                    response.failure("Ответ сервера не является валидным JSON")
            else:
                response.failure(f"Ошибка чтения системы: HTTP {response.status_code}")

    @task(2)
    def get_chassis_info(self):
        if not self.token: return
        with self.client.get("/redfish/v1/Chassis", headers={"X-Auth-Token": self.token}, verify=False, catch_response=True) as response:
            response.success() if response.status_code == 200 else response.failure(f"HTTP {response.status_code}")

    @task(2)
    def get_event_logs(self):
        if not self.token: return
        with self.client.get("/redfish/v1/Managers/bmc/LogServices/Journal/Entries", headers={"X-Auth-Token": self.token}, verify=False, catch_response=True) as response:
            response.success() if response.status_code == 200 else response.failure(f"HTTP {response.status_code}")

    @task(1)
    def get_service_root(self):
        if not self.token: return
        with self.client.get("/redfish/v1", headers={"X-Auth-Token": self.token}, verify=False, catch_response=True) as response:
            response.success() if response.status_code == 200 else response.failure(f"HTTP {response.status_code}")
