import sys
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.bash import BashOperator
from datetime import datetime, timedelta

sys.path.append('/opt/airflow/api_call')

def safe_main_callable():
    from insert_records import main
    return main()

default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

DBT_CMD = (
    'docker run --rm '
    '--network data_project_my_network '
    '-v /home/srujan/repos/data_project/dbt:/usr/app '
    '-w /usr/app '
    '-e DBT_PROFILES_DIR=/usr/app '
    'ghcr.io/dbt-labs/dbt-postgres:1.9.latest '
)

dag = DAG(
    dag_id='weather-api-dbt-orchestrator',
    default_args=default_args,
    description='Ingest weather data, transform with dbt, validate data quality',
    start_date=datetime(2024, 4, 30),
    schedule=timedelta(minutes=5),
    catchup=False,
    is_paused_upon_creation=False,
)

with dag:
    task1 = PythonOperator(
        task_id='ingest_data_task',
        python_callable=safe_main_callable,
    )

    task2 = BashOperator(
        task_id='transform_data_task',
        bash_command=DBT_CMD + 'run',
    )

    task3 = BashOperator(
        task_id='validate_data_task',
        bash_command=DBT_CMD + 'test',
    )

    task1 >> task2 >> task3