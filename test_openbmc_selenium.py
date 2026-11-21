import json
import time
import unittest
from base64 import b64encode
 
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
 
BASE_URL = "https://localhost:2443"
USERNAME = "root"
PASSWORD = "0penBmc"
WRONG_PASSWORD = "WrongPass123"
TEST_USER = "labtest"
TEST_PASSWORD = "TestPass123"
 
 
class TestOpenBMC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--allow-insecure-localhost")
        options.binary_location = "/usr/bin/chromium-browser"
        cls.driver = webdriver.Chrome(options=options)
        cls.driver.set_page_load_timeout(60)
        cls.driver.set_script_timeout(45)
        cls.basic = b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        cls.driver.execute_cdp_cmd("Network.enable", {})
        cls.driver.get(BASE_URL + "/redfish/v1")
 
    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
 
    def setUp(self):
        self._extra_headers({})
 
    def _extra_headers(self, headers):
        self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": headers})
 
    def _api(self, method, path, body=None, token=None, basic=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auth-Token"] = token
        if basic:
            headers["Authorization"] = "Basic " + self.basic
        payload = json.dumps(body) if body is not None else None
        script = """
        const [method, url, headers, payload, done] = arguments;
        const opts = {method: method, headers: headers, cache: 'no-store'};
        if (payload !== null) { opts.body = payload; }
        fetch(url, opts).then(async r => {
            const text = await r.text();
            let parsed = null;
            try { parsed = JSON.parse(text); } catch (e) {}
            done({status: r.status,
                  token: r.headers.get('x-auth-token'),
                  location: r.headers.get('location'),
                  json: parsed,
                  text: text.slice(0, 400)});
        }).catch(e => done({status: 0, token: null, location: null, json: null, text: String(e)}));
        """
        return self.driver.execute_async_script(script, method, BASE_URL + path, headers, payload)
 
    def _login(self, password=PASSWORD):
        return self._login_as(USERNAME, password)
 
    def _login_as(self, name, password):
        return self._api("POST", "/redfish/v1/SessionService/Sessions",
                         {"UserName": name, "Password": password}, basic=False)
 
    def _logout(self, token, location):
        path = location if location else "/redfish/v1/SessionService/Sessions"
        return self._api("DELETE", path, token=token, basic=False)["status"]
 
    def _policy(self):
        r = self._api("GET", "/redfish/v1/AccountService")
        self.assertEqual(r["status"], 200)
        return r["json"]["AccountLockoutThreshold"], r["json"]["AccountLockoutDuration"]
 
    def _set_policy(self, threshold, duration):
        return self._api("PATCH", "/redfish/v1/AccountService",
                         {"AccountLockoutThreshold": threshold,
                          "AccountLockoutDuration": duration})["status"]
 
    def _create_user(self, name, password, role="ReadOnly"):
        return self._api("POST", "/redfish/v1/AccountService/Accounts",
                         {"UserName": name, "Password": password,
                          "RoleId": role, "Enabled": True})
 
    def _delete_user(self, name):
        return self._api("DELETE", f"/redfish/v1/AccountService/Accounts/{name}")["status"]
 
    def _account(self, name):
        r = self._api("GET", f"/redfish/v1/AccountService/Accounts/{name}")
        return r["status"], r["json"]
 
    def _power_state(self):
        r = self._api("GET", "/redfish/v1/Systems/system")
        self.assertEqual(r["status"], 200)
        return r["json"]["PowerState"]
 
    def _chassis_state(self):
        r = self._api("GET", "/redfish/v1/Chassis/chassis")
        self.assertEqual(r["status"], 200)
        return r["json"].get("PowerState")
 
    def _reset(self, reset_type):
        code = self._api("POST", "/redfish/v1/Systems/system/Actions/ComputerSystem.Reset",
                         {"ResetType": reset_type})["status"]
        print(f"    POST Reset {reset_type} -> HTTP {code}")
        return code
 
    def _observe(self, seconds=150, interval=1.0, until=None):
        system_seq, chassis_seq = [], []
        deadline = time.time() + seconds
        while time.time() < deadline:
            s, c = self._power_state(), self._chassis_state()
            if not system_seq or system_seq[-1] != s:
                system_seq.append(s)
                print(f"    Systems.PowerState = {s}")
            if not chassis_seq or chassis_seq[-1] != c:
                chassis_seq.append(c)
                print(f"    Chassis.PowerState = {c}")
            if until and until(s, c):
                break
            time.sleep(interval)
        return system_seq, chassis_seq
 
    def _journal_page(self, path):
        if path.startswith("http"):
            path = path[len(BASE_URL):]
        script = """
        const [url, headers, done] = arguments;
        fetch(url, {headers: headers, cache: 'no-store'}).then(async r => {
            let j = null;
            try { j = await r.json(); } catch (e) {}
            done({status: r.status, json: j});
        }).catch(e => done({status: 0, json: null}));
        """
        headers = {"Authorization": "Basic " + self.basic}
        return self.driver.execute_async_script(script, BASE_URL + path, headers)
 
    def _journal_snapshot(self, path, max_pages=10):
        first = self._journal_page(path)
        if first["status"] != 200 or not first["json"]:
            return None
 
        def parse(page):
            members = page.get("Members", [])
            ids = [e.get("Id") or e.get("@odata.id") for e in members]
            msgs = {i: e.get("Message", "") for i, e in zip(ids, members)}
            return ids, msgs
 
        page = first["json"]
        head_ids, head_msgs = parse(page)
        tail_ids, tail_msgs = head_ids, head_msgs
        link = page.get("Members@odata.nextLink")
        pages = 1
        while link and pages < max_pages:
            nxt = self._journal_page(link)
            if nxt["status"] != 200 or not nxt["json"]:
                break
            page = nxt["json"]
            if not page.get("Members"):
                break
            tail_ids, tail_msgs = parse(page)
            link = page.get("Members@odata.nextLink")
            pages += 1
 
        return {"count": page.get("Members@odata.count"),
                "pages": pages,
                "ids": set(head_ids) | set(tail_ids),
                "messages": {**head_msgs, **tail_msgs}}
 
    def test_01_successful_auth(self):
        """Тест 1: вход по логину и паролю, доступ по токену, выход"""
        r = self._login()
        print(f"  POST Sessions -> HTTP {r['status']}, location={r['location']}")
        self.assertEqual(r["status"], 201, "Вход с верными данными не выполнен")
        self.assertTrue(r["token"], "Сервер не выдал X-Auth-Token")
 
        self._extra_headers({"X-Auth-Token": r["token"]})
        self.driver.get(BASE_URL + "/redfish/v1/Systems")
        page = self.driver.page_source
        self._extra_headers({})
        self.assertIn("Computer System Collection", page)
        print(f"  Страница Systems по токену: {len(page)} байт, коллекция систем найдена")
 
        code = self._logout(r["token"], r["location"])
        print(f"  DELETE сессии -> HTTP {code}")
        self.assertIn(code, (200, 204))
 
        self._extra_headers({"X-Auth-Token": r["token"]})
        self.driver.get(BASE_URL + "/redfish/v1/Systems")
        page = self.driver.page_source
        self._extra_headers({})
        print(f"  GET Systems с отозванным токеном: {len(page)} байт")
        self.assertNotIn("Computer System Collection", page,
                         "Отозванный токен продолжает давать доступ")
 
    def test_02_invalid_auth(self):
        """Тест 2: отказ во входе при неверном пароле"""
        r = self._login(WRONG_PASSWORD)
        print(f"  POST Sessions с неверным паролем -> HTTP {r['status']}, token={r['token']}")
        print(f"  Ответ: {r['text'][:200]}")
        self.assertEqual(r["status"], 401, "Неверный пароль принят сервером")
        self.assertIsNone(r["token"], "При отказе выдан токен")
        self.assertIn("error", r["json"] or {})
 
    def test_03_account_lockout(self):
        """Тест 3: блокировка учётной записи и авторазблокировка"""
        original = self._policy()
        print(f"  Исходная политика: threshold={original[0]}, duration={original[1]}")
        created = False
        try:
            self.assertIn(self._set_policy(3, 30), (200, 204), "Политика блокировки не изменяется")
            self.assertEqual(self._policy(), (3, 30))
            print("  Политика установлена: threshold=3, duration=30")
 
            r = self._create_user(TEST_USER, TEST_PASSWORD)
            print(f"  Создание учётной записи {TEST_USER} -> HTTP {r['status']}")
            self.assertEqual(r["status"], 201, "Не удалось создать тестовую учётную запись")
            created = True
 
            r = self._login_as(TEST_USER, TEST_PASSWORD)
            self.assertEqual(r["status"], 201, "Тестовая учётная запись не работает")
            self._logout(r["token"], r["location"])
 
            for i in range(3):
                r = self._login_as(TEST_USER, WRONG_PASSWORD)
                print(f"    Попытка {i + 1} с неверным паролем -> HTTP {r['status']}")
                self.assertEqual(r["status"], 401)
 
            r = self._login_as(TEST_USER, TEST_PASSWORD)
            print(f"    Вход с верным паролем при блокировке -> HTTP {r['status']}")
            self.assertEqual(r["status"], 401,
                             "Заблокированная учётная запись пустила по верному паролю")
 
            status, acc = self._account(TEST_USER)
            self.assertEqual(status, 200)
            print(f"    Locked = {acc.get('Locked')}")
            self.assertTrue(acc.get("Locked"), "Учётная запись не помечена как заблокированная")
 
            print("    Ожидание истечения AccountLockoutDuration (30 с)...")
            time.sleep(35)
            r = self._login_as(TEST_USER, TEST_PASSWORD)
            print(f"    Вход после ожидания -> HTTP {r['status']}")
            self.assertEqual(r["status"], 201, "Учётная запись не разблокировалась автоматически")
            self._logout(r["token"], r["location"])
        finally:
            if created:
                print(f"  Удаление учётной записи -> HTTP {self._delete_user(TEST_USER)}")
            for _ in range(6):
                if self._set_policy(*original) in (200, 204):
                    break
                time.sleep(10)
            print(f"  Политика восстановлена: {self._policy()}")
 
    def test_04_power_control(self):
        """Тест 4: включение и выключение сервера через веб-интерфейс (TC3-1)"""
        print(f"  Исходно: Systems={self._power_state()}, Chassis={self._chassis_state()}")
 
        print("  Фаза 1: включение (ForceOn -> Host.On)")
        self.assertIn(self._reset("ForceOn"), (200, 202, 204), "Команда ForceOn не принята")
        sys_on, ch_on = self._observe(
            seconds=150, until=lambda s, c: s == "On" or c == "On")
        started = ("PoweringOn" in sys_on) or ("On" in ch_on) or ("On" in sys_on)
        self.assertTrue(started,
                        f"Переход включения не наблюдался: Systems={sys_on}, Chassis={ch_on}")
        print(f"    Systems: {' -> '.join(sys_on)}")
        print(f"    Chassis: {' -> '.join(str(x) for x in ch_on)}")
 
        print("  Фаза 2: выключение (ForceOff -> Chassis.Off)")
        self.assertIn(self._reset("ForceOff"), (200, 202, 204), "Команда ForceOff не принята")
        sys_off, ch_off = self._observe(
            seconds=150, until=lambda s, c: c == "Off")
        self.assertIn("Off", ch_off,
                      f"Шасси не выключилось: Systems={sys_off}, Chassis={ch_off}")
        print(f"    Systems: {' -> '.join(sys_off)}")
        print(f"    Chassis: {' -> '.join(str(x) for x in ch_off)}")
 
        self.assertEqual(self._api("GET", "/redfish/v1/Systems/system")["status"], 200,
                         "BMC недоступен после операций с питанием")
        print(f"  Итог: Systems={self._power_state()}, Chassis={self._chassis_state()}, BMC доступен")
 
    def test_05_event_after_action(self):
        """Тест 5: событие в журнале после выполненного действия (TC3-4)"""
        path = "/redfish/v1/Managers/bmc/LogServices/Journal/Entries"
        before = self._journal_snapshot(path)
        self.assertIsNotNone(before, "Журнал Journal недоступен через веб")
        print(f"  Журнал до действия: count={before['count']}, "
              f"страниц={before['pages']}, уникальных Id={len(before['ids'])}")
 
        state = self._chassis_state()
        action = "ForceOff" if state != "Off" else "ForceOn"
        print(f"  Состояние шасси {state}, выполняется действие {action}")
        self.assertIn(self._reset(action), (200, 202, 204))
 
        new_ids, after = [], None
        deadline = time.time() + 90
        while time.time() < deadline and not new_ids:
            time.sleep(5)
            head = self._journal_page(path)
            if head["status"] == 200 and head["json"]:
                ids = {e.get("Id") or e.get("@odata.id")
                       for e in head["json"].get("Members", [])}
                fresh = ids - before["ids"]
                if fresh:
                    after = self._journal_snapshot(path)
                    new_ids = sorted(fresh)
                    break
            after = self._journal_snapshot(path)
            if after:
                fresh = after["ids"] - before["ids"]
                if fresh:
                    new_ids = sorted(fresh)
                    break
 
        self.assertTrue(new_ids,
                        f"После действия {action} в журнале не появилось новых записей")
 
        texts = [after["messages"].get(i, "") for i in new_ids]
        print(f"  Новых записей: {len(new_ids)}")
        for t in texts[:15]:
            print(f"    {t[:160]}")
 
        keys = ("power", "chassis", "host", "state", "transition", "reboot")
        matched = [t for t in texts if any(k in t.lower() for k in keys)]
        self.assertTrue(matched, "Среди новых записей нет событий перехода питания")
        print(f"  Из них связаны с переходом питания: {len(matched)}")
 
 
if __name__ == "__main__":
    unittest.main(verbosity=2)
