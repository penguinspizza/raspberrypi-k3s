import yaml
from jinja2 import Environment, FileSystemLoader
import ipaddress
import os
import base64
import crypt
import uuid
import argparse
import logging
from cerberus import Validator
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

# ログの設定
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 定数としてファイルパスを定義
CONFIG_YAML_FILEPATH = './config.yaml'
MASTER_USERDATA_TMPL_FILEPATH = './template/master/user-data.j2'
MASTER_NETWORKCONFIG_TMPL_FILEPATH = './template/master/network-config.j2'
WORKER_USERDATA_TMPL_FILEPATH = './template/worker/user-data.j2'
WORKER_NETWORKCONFIG_TMPL_FILEPATH = './template/worker/network-config.j2'
K3S_SETUP_TMPL_FILEPATH = './template/master/k3s_setup.j2'

# YAMLスキーマを定義
# このスキーマは、YAMLファイルが期待される形式と一致しているかを検証するために使用されます。
YAML_SCHEMA = {
    'common': {'type': 'dict', 'schema': {
        'username': {'type': 'string', 'required': True},
        'password': {'type': 'string', 'required': True},
    }},
    'master': {'type': 'dict', 'schema': {
        'hostname': {'type': 'string', 'required': True},
        'internal': {'type': 'dict', 'schema': {
            'ip': {'type': 'string', 'required': True},
            'netmask': {'type': 'string', 'required': True},
            'mac': {'type': 'string', 'required': True},
            'dhcpd': {'type': 'dict', 'schema': {
                'domain': {'type': 'string', 'required': True},
                'range': {'type': 'dict', 'schema': {
                    'start': {'type': 'string', 'required': True},
                    'end': {'type': 'string', 'required': True}
                }}
            }},
        }},
        'external': {'type': 'dict', 'schema': {
            'ip': {'type': 'string', 'required': True},
            'netmask': {'type': 'string', 'required': True},
            'interface': {'type': 'string', 'required': True}
        }},
    }},
    'workers': {'type': 'list', 'required': True, 'schema': {
        'type': 'dict', 'schema': {
            'hostname': {'type': 'string', 'required': True},
            'ip': {'type': 'string', 'required': True},
            'mac': {'type': 'string', 'required': True}
        }
    }}
}

# YAMLファイルをバリデートする関数
# スキーマを使って、YAMLデータが正しい形式かどうかをチェックします。
# バリデーションエラーがあれば例外を投げます。
def validate_yaml(context):
    validator = Validator(YAML_SCHEMA)
    if not validator.validate(context):
        raise ValueError(f"YAML validation error: {validator.errors}")

# --- NetworkManager ---
class NetworkManager:
    """ ネットワークアドレスの計算に関する処理を担当するクラス """
    
    @staticmethod
    def derive_network_address(ip, netmask):
        """IPアドレスとネットマスクを基にネットワークアドレスを導出"""
        network = ipaddress.IPv4Network(f'{ip}/{netmask}', strict=False)
        return str(network.network_address)

    @staticmethod
    def derive_broadcast_address(ip, netmask):
        """IPアドレスとネットマスクを基にブロードキャストアドレスを導出"""
        network = ipaddress.IPv4Network(f'{ip}/{netmask}', strict=False)
        return str(network.broadcast_address)


# --- SSHKeyGenerator ---
class SSHKeyGenerator:
    """ Ed25519形式のSSH秘密鍵と公開鍵を生成するクラス """
    
    @staticmethod
    def generate():
        private_key = ed25519.Ed25519PrivateKey.generate()
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption()
        )
        public_key = private_key.public_key()
        public_key_ssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        )
        return private_key_pem.decode(), public_key_ssh.decode()


# --- TokenGenerator ---
class TokenGenerator:
    """ ランダムなトークンを生成するクラス """
    
    @staticmethod
    def generate_token():
        random_bytes = os.urandom(32)
        return base64.b64encode(random_bytes).decode('utf-8')


# --- ConfigData ---
class ConfigData:
    """ YAMLファイルの読み込みと処理を行うクラス """

    def __init__(self, yaml_file, ssh_key_generator=None, network_manager=None, token_generator=None):
        self.context = self.load_yaml(yaml_file)
        self.ssh_key_generator = ssh_key_generator or SSHKeyGenerator()
        self.network_manager = network_manager or NetworkManager()
        self.token_generator = token_generator or TokenGenerator()
        self.uuid_str = str(uuid.uuid4())  # ファイル出力に使用するUUIDを生成

    @staticmethod
    def load_yaml(yaml_file):
        """ YAMLファイルを読み込んで辞書形式で返す """
        with open(yaml_file, 'r') as file:
            return yaml.safe_load(file)

    def process(self):
        """ YAMLファイルのデータを処理し、SSHキーやトークン、ネットワークアドレスを生成する """

        # YAMLファイルのバリデーション
        validate_yaml(self.context)
        
        # マスターのネットワーク情報の導出
        master = self.context['master']
        master['external']['network_address'] = self.network_manager.derive_network_address(master['external']['ip'], master['external']['netmask'])
        master['internal']['network_address'] = self.network_manager.derive_network_address(master['internal']['ip'], master['internal']['netmask'])
        master['internal']['broadcast_address'] = self.network_manager.derive_broadcast_address(master['internal']['ip'], master['internal']['netmask'])

        # SSHキーとトークンの生成
        private_key, public_key = self.ssh_key_generator.generate()
        self.context['generated_key'] = private_key
        self.context['generated_public_key'] = public_key
        self.context['generated_token'] = self.token_generator.generate_token()

        # パスワードのハッシュ化
        self.context['common']['hashed_password'] = self.hash_password(self.context['common']['password'])

    @staticmethod
    def hash_password(password):
        """ パスワードをSHA-256でハッシュ化 """
        salt = crypt.mksalt(crypt.METHOD_SHA256)
        return crypt.crypt(password, salt)


# --- FileGenerator ---
class FileGenerator:
    """ テンプレートファイルを使用して設定ファイルを生成するクラス """
    
    def __init__(self, template_env):
        self.template_env = template_env

    def generate_file(self, template_file, context, output_file):
        """ テンプレートファイルを使用して設定ファイルを生成し、指定された場所に保存 """
        try:
            template = self.template_env.get_template(template_file)
            content = template.render(context)
            os.makedirs(os.path.dirname(output_file), exist_ok=True)

            with open(output_file, 'w') as file:
                file.write(content)
            logging.info(f"File {output_file} generated successfully.")
        except Exception as e:
            logging.error(f"Error generating file {output_file}: {e}")
            raise

    def generate_worker_files(self, worker_template_file, network_template_file, context, uuid_str):
        """ ワーカーノード用の設定ファイルを生成する """
        workers = context['workers']
        for worker in workers:
            worker_context = context.copy()
            worker_context['worker'] = worker
            worker_context['worker']['ssh_authorized_keys'] = context['generated_public_key']

            output_file = f"./output/{uuid_str}/{worker['hostname']}/user-data"
            network_output_file = f"./output/{uuid_str}/{worker['hostname']}/network-config"

            self.generate_file(worker_template_file, worker_context, output_file)
            self.generate_file(network_template_file, worker_context, network_output_file)

    def generate_master_files(self, master_template_file, master_network_template_file, manual_setup_template_file, context, uuid_str):
        """ マスター用設定ファイルとk3s_setup.shの生成 """
        master_output_file = f"./output/{uuid_str}/{context['master']['hostname']}/user-data"
        master_network_output_file = f"./output/{uuid_str}/{context['master']['hostname']}/network-config"
        self.generate_file(master_template_file, context, master_output_file)
        self.generate_file(master_network_template_file, context, master_network_output_file)

        # manual-link-upが指定された場合のk3s_setup.sh生成
        if context['manual_link_up']:
            k3s_manual_setup_output_file = f"./output/{uuid_str}/{context['master']['hostname']}/k3s_setup.sh"
            self.generate_file(manual_setup_template_file, context, k3s_manual_setup_output_file)


# --- Main 実行部分 ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='テンプレート生成スクリプト')
    parser.add_argument('--manual-link-up', action='store_true', help='manual-link-upオプションを指定')
    args = parser.parse_args()

    # コンフィグデータの処理
    config = ConfigData(CONFIG_YAML_FILEPATH)
    config.process()

    # コマンドライン引数に応じてmanual_link_upを設定
    config.context['manual_link_up'] = args.manual_link_up

    # Jinja2テンプレート環境の設定
    file_loader = FileSystemLoader('.')
    template_env = Environment(loader=file_loader)
    template_env.filters['netmask_to_cidr'] = lambda netmask: ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen

    # ファイル生成クラスの初期化
    file_generator = FileGenerator(template_env)

    # マスター用設定ファイルとk3s_setup.shの生成
    file_generator.generate_master_files(MASTER_USERDATA_TMPL_FILEPATH, MASTER_NETWORKCONFIG_TMPL_FILEPATH, K3S_SETUP_TMPL_FILEPATH, config.context, config.uuid_str)

    # ワーカーノード用ファイルの生成
    file_generator.generate_worker_files(WORKER_USERDATA_TMPL_FILEPATH, WORKER_NETWORKCONFIG_TMPL_FILEPATH, config.context, config.uuid_str)
