# ベースイメージとしてPython 3.8を使用
FROM python:3.8-slim

# 作業ディレクトリを設定
WORKDIR /app

# 必要なファイルをコンテナにコピー
COPY . /app

# 必要なPythonライブラリをインストール
RUN pip install --no-cache-dir -r requirements.txt

# コンテナ起動時に任意の引数を渡せるようにENTRYPOINTを設定
ENTRYPOINT ["python3", "main.py"]
