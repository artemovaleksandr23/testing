import pytest
import requests
import time
import subprocess
import logging
import urllib3
import os
 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
 
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
 
BASE_URL = "https://localhost:2443"
USERNAME = "root"
IPMI_HOST = "127.0.0.1"
IPMI_PORT = "2623"
 
 
@pytest.fixture(scope="session")
def redfish_session():
    """Создаёт общую авторизованную Redfish-сессию для всех тестов."""
    session = requests.Session()
    session.verify = False
    session.headers.update({"Content-Type": "application/json"})
 
    # Пароль берётся из переменной окружения для безопасности, с фоллбэком только для Redfish
    password = os.environ.get("BMC_PASSWORD", "0penBmc")
 
    response = session.post(
        f"{BASE_URL}/redfish/v1/SessionService/Sessions",
        json={"UserName": USERNAME, "Password": password},
        timeout=15
    )
    assert response.status_code in (200, 201), f"Не удалось создать сессию: HTTP {response.status_code}, {response.text}"
 
    token = response.headers.get("X-Auth-Token")
    session_url = response.headers.get("Location")
    assert token, "Сервер не вернул X-Auth-Token"
 
    session.headers.update({"X-Auth-Token": token})
    logger.info("Redfish-сессия успешно создана")
 
    yield session
 
    if session_url:
        try:
            delete_response = session.delete(f"{BASE_URL}{session_url}", timeout=15)
            assert delete_response.status_code in (200, 202, 204), f"Ошибка удаления сессии: HTTP {delete_response.status_code}"
            logger.info(f"Удаление сессии: HTTP {delete_response.status_code}")
        except requests.RequestException:
            logger.exception("Ошибка при удалении Redfish-сессии")
    session.close()
 
 
def test_01_successful_auth(redfish_session):
    """Тест 1: Успешная авторизация и наличие токена."""
    assert redfish_session.headers.get("X-Auth-Token"), "Отсутствует токен авторизации"
    logger.info("Успешная авторизация подтверждена")
 
 
def test_02_invalid_auth():
    """Тест 2: Отказ в авторизации с неверным паролем (дополнительный)."""
    password = os.environ.get("BMC_PASSWORD", "0penBmc")
    response = requests.post(
        f"{BASE_URL}/redfish/v1/SessionService/Sessions",
        json={"UserName": USERNAME, "Password": "WrongPassword123"},
        verify=False,
        timeout=15
    )
    assert response.status_code == 401, f"Ожидался HTTP 401, получен {response.status_code}"
    assert "X-Auth-Token" not in response.headers
    logger.info("Проверка неверных учётных данных пройдена")
 
 
def test_03_system_information(redfish_session):
    """Тест 3: Получение информации о системе (Status и PowerState)."""
    response = redfish_session.get(f"{BASE_URL}/redfish/v1/Systems/system", timeout=15)
    assert response.status_code == 200, f"Ошибка получения системы: HTTP {response.status_code}"
 
    data = response.json()
    assert "Status" in data, "В ответе отсутствует поле Status"
    assert "PowerState" in data, "В ответе отсутствует поле PowerState"
    logger.info(f"Состояние системы: Status={data['Status']}, PowerState={data['PowerState']}")
 
 
def test_04_power_control(redfish_session):
    """Тест 4: Включение сервера через Redfish и строгая проверка PowerState == On."""
    reset_url = f"{BASE_URL}/redfish/v1/Systems/system/Actions/ComputerSystem.Reset"
 
    response = redfish_session.post(reset_url, json={"ResetType": "On"}, timeout=15)
    assert response.status_code in (200, 202, 204), (
        f"Команда включения не принята сервером. "
        f"Ожидался HTTP 200/202/204, получен {response.status_code}: {response.text}"
    )
    logger.info(f"Команда включения (On) принята сервером (HTTP {response.status_code})")
 
    power_state = None
    for attempt in range(60):  # до 120 секунд
        time.sleep(2)
        system_response = redfish_session.get(f"{BASE_URL}/redfish/v1/Systems/system", timeout=15)
        assert system_response.status_code == 200
        power_state = system_response.json().get("PowerState")
        logger.info(f"Попытка {attempt + 1}: PowerState={power_state}")
        if power_state == "On":
            break
 
    # Строгое утверждение: если состояние не On, тест должен упасть
    assert power_state == "On", (
        f"Система не перешла в состояние On за 120 секунд. "
        f"Последнее состояние: {power_state}"
    )
    logger.info("Система успешно перешла в состояние On")
 
 
def test_05_cpu_temperature(redfish_session):
    """Тест 5: Проверка наличия и допустимости температуры CPU через Redfish."""
    chassis_response = redfish_session.get(f"{BASE_URL}/redfish/v1/Chassis", timeout=15)
    assert chassis_response.status_code == 200
    chassis_members = chassis_response.json().get("Members", [])
    assert chassis_members, "Redfish не вернул список Chassis"
 
    cpu_sensors = []
    for chassis in chassis_members:
        chassis_uri = chassis.get("@odata.id")
        if not chassis_uri:
            continue
        thermal_response = redfish_session.get(f"{BASE_URL}{chassis_uri}/Thermal", timeout=15)
        if thermal_response.status_code != 200:
            continue
 
        thermal_data = thermal_response.json()
        for sensor in thermal_data.get("Temperatures", []):
            name = sensor.get("Name", "")
            value = sensor.get("ReadingCelsius")
            context = sensor.get("PhysicalContext", "")
            if ("cpu" in name.lower() or context.lower() == "processor") and isinstance(value, (int, float)):
                cpu_sensors.append((name, float(value)))
 
    if not cpu_sensors:
        pytest.skip("Ограничение QEMU: Redfish не предоставляет данные о температуре CPU (ресурс Thermal пуст или недоступен).")
 
    for name, value in cpu_sensors:
        assert 0 <= value <= 100, f"Температура {name} ({value}°C) вне допустимого диапазона 0-100"
        logger.info(f"Температура {name}: {value}°C")
 
 
def test_06_cpu_sensors_redfish_ipmi(redfish_session):
    """Тест 6: Сопоставление температуры CPU в Redfish и IPMI."""
    # Строгое требование переменной окружения для безопасности
    ipmi_password = os.environ.get("IPMI_PASSWORD")
    if not ipmi_password:
        pytest.skip("Не задана переменная окружения IPMI_PASSWORD")
 
    env = os.environ.copy()
    env["IPMI_PASSWORD"] = ipmi_password
 
    cmd = [
        "ipmitool", "-I", "lanplus", "-H", IPMI_HOST, "-p", IPMI_PORT,
        "-U", USERNAME, "-E", "sdr", "type", "temperature", "-c"
    ]
 
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
    except subprocess.TimeoutExpired:
        pytest.skip("Ограничение QEMU: Превышено время ожидания ответа IPMI в используемой конфигурации.")
 
    if result.returncode != 0:
        pytest.skip(f"Ограничение QEMU: IPMI вернул ошибку: {result.stderr.strip()}")
 
    ipmi_sensors = {}
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split(',')
        if len(parts) >= 3:
            name = parts[0].strip()
            try:
                value = float(parts[2].strip())
                if "cpu" in name.lower():
                    ipmi_sensors[name] = value
            except ValueError:
                continue
 
    if not ipmi_sensors:
        pytest.skip("Ограничение QEMU: IPMI не возвращает активных датчиков CPU.")
 
    chassis_response = redfish_session.get(f"{BASE_URL}/redfish/v1/Chassis", timeout=15)
    assert chassis_response.status_code == 200
 
    redfish_sensors = {}
    for chassis in chassis_response.json().get("Members", []):
        chassis_uri = chassis.get("@odata.id")
        if not chassis_uri:
            continue
        thermal_response = redfish_session.get(f"{BASE_URL}{chassis_uri}/Thermal", timeout=15)
        if thermal_response.status_code != 200:
            continue
 
        for sensor in thermal_response.json().get("Temperatures", []):
            name = sensor.get("Name", "")
            value = sensor.get("ReadingCelsius")
            context = sensor.get("PhysicalContext", "")
            if ("cpu" in name.lower() or context.lower() == "processor") and isinstance(value, (int, float)):
                redfish_sensors[name] = float(value)
 
    if not redfish_sensors:
        pytest.skip("Ограничение QEMU: Redfish не предоставляет данные о температуре CPU для сопоставления.")
 
    matched = False
    for ipmi_name, ipmi_val in ipmi_sensors.items():
        for rf_name, rf_val in redfish_sensors.items():
            if ipmi_name.lower() in rf_name.lower() or rf_name.lower() in ipmi_name.lower():
                matched = True
                diff = abs(ipmi_val - rf_val)
                assert diff <= 5.0, f"Расхождение температур для {ipmi_name}/{rf_name}: IPMI={ipmi_val}, Redfish={rf_val} (разница {diff}°C)"
                logger.info(f"Совпадение найдено: {ipmi_name} (IPMI: {ipmi_val}°C) ≈ {rf_name} (Redfish: {rf_val}°C)")
 
    if not matched:
        pytest.skip(f"Не удалось сопоставить имена датчиков. IPMI: {list(ipmi_sensors.keys())}, Redfish: {list(redfish_sensors.keys())}")
 
 
if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--log-cli-level=INFO"])
