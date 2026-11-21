pipeline {
    agent any

    environment {
        BMC_PASSWORD = '0penBmc'
        DEBIAN_FRONTEND = 'noninteractive'
    }

    stages {
        stage('Checkout & Prepare') {
            steps {
                echo 'Клонирование и установка зависимостей...'
                checkout scm
                sh '''
                    apt-get update -qq
                    apt-get install -y -qq qemu-system-arm python3 python3-pip python3-venv chromium chromium-driver curl
                    
                    python3 -m venv venv
                    . venv/bin/activate
                    pip install --upgrade pip -q
                    pip install pytest requests locust selenium -q
                '''
            }
        }

        stage('Start OpenBMC QEMU') {
            steps {
                echo 'Запуск QEMU с OpenBMC...'
                sh '''
                    nohup qemu-system-arm -m 256 -M romulus-bmc -nographic \
                    -drive file=obmc-phosphor-image-romulus.static.mtd,format=raw,if=mtd \
                    -net nic -net user,hostfwd=tcp::2443-:443,hostfwd=tcp::2222-:22 \
                    > qemu_boot.log 2>&1 &
                    echo $! > qemu.pid
                    
                    echo 'Ожидание инициализации Redfish API (порт 2443)...'
                    timeout 240 bash -c 'until curl -k -s https://localhost:2443/redfish/v1 > /dev/null; do sleep 5; done'
                    echo 'OpenBMC успешно загрузился и отвечает!'
                '''
            }
        }

        stage('API Tests (PyTest)') {
            steps {
                sh '''
                    . venv/bin/activate
                    pytest test_redfish.py --junitxml=pytest_results.xml || true
                '''
            }
            post { always { junit 'pytest_results.xml' } }
        }

        stage('WebUI Tests (Selenium)') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 test_openbmc_selenium.py > selenium_results.txt 2>&1 || true
                '''
            }
            post { always { archiveArtifacts artifacts: 'selenium_results.txt', allowEmptyArchive: true } }
        }

        stage('Load Testing (Locust)') {
            steps {
                sh '''
                    . venv/bin/activate
                    locust -f locustfile.py --headless -u 5 -r 1 -t 15s \
                    --host=https://localhost:2443 --html=locust_report.html --csv=locust_results || true
                '''
            }
            post { always { archiveArtifacts artifacts: 'locust_report.html, locust_results*.csv', allowEmptyArchive: true } }
        }
    }

    post {
        always {
            echo 'Очистка: завершение QEMU...'
            sh '''
                if [ -f qemu.pid ]; then kill $(cat qemu.pid) || true; rm qemu.pid; fi
                pkill -f qemu-system-arm || true
            '''
            archiveArtifacts artifacts: 'qemu_boot.log', allowEmptyArchive: true
        }
    }
}
