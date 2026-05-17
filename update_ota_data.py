import os
import json
import time
import requests
import re
from bs4 import BeautifulSoup
import google.generativeai as genai

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("Error: GEMINI_API_KEY is not set.")

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel(
    'gemini-3.1-flash-lite-preview',
    generation_config={"response_mime_type": "application/json"}
)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TARGET_URL = "https://www.city.ota.gunma.jp/site/gomi/1056442.html"
DOMAIN = "https://www.city.ota.gunma.jp"
DATA_DIR = "data"
TOWN_DIR = os.path.join(DATA_DIR, "towns")
INDEX_FILE = os.path.join(DATA_DIR, "index.json")

# 除外事故を防ぐため、対象となる日本語版エリアを完全指定
TARGET_AREAS = [
    "太田エリア版", "尾島Aエリア版", "尾島Bエリア版",
    "新田北部エリア版", "新田南部エリア版", "藪塚本町エリア版"
]

def get_pdf_links():
    print("太田市HPにアクセス中...")
    response = requests.get(TARGET_URL, headers=HEADERS)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    pdf_list = []
    
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        text = a_tag.get_text(strip=True)
        
        if "PDF" in text or "pdf" in href.lower():
            for target_area in TARGET_AREAS:
                if target_area in text:
                    # 括弧（Portuguêsなど）を含むものは外国語版なので除外
                    if "（" not in text and "(" not in text:
                        pdf_url = href if href.startswith('http') else DOMAIN + href
                        pdf_list.append({"area": target_area, "url": pdf_url})
                    break
    return pdf_list

def process_pdf_with_gemini(area_name, pdf_url):
    temp_pdf_path = f"temp_{area_name}.pdf"
    try:
        print(f"[{area_name}] PDFダウンロード中...")
        res = requests.get(pdf_url, headers=HEADERS)
        res.raise_for_status()
        with open(temp_pdf_path, 'wb') as f:
            f.write(res.content)
            
        print(f"[{area_name}] Geminiへアップロード中...")
        uploaded_file = genai.upload_file(path=temp_pdf_path, mime_type="application/pdf")
        
        time.sleep(5)
        
        print(f"[{area_name}] UIカレンダー用データを解析中...")
        prompt = f"""
        あなたはデータエンジニアです。太田市の「{area_name}」のゴミ収集カレンダーPDFを解析し、以下のJSONフォーマットで出力してください。

        【解析ルール・最重要】
        PDFのレイアウト上、文字が分断されたり誤認識されたりしています。以下の表記ゆれや分断を脳内で結合し、指定の記号に変換してください。
        - "M" (燃えるゴミ): 「もえるごみ」「もえる」
        - "N" (燃えないゴミ): 「もえないごみ」「もえない」
        - "R" (ペットボトル): 「ペット ボトル」「ペット」「ボトル」が離れて配置されている場合
        - "C" (カン): 「力 ン」「カ ン」「カン」(漢字の「力」になっている場合を含む)
        - "B" (ビン): 「ビ ン」「ヒ ン」「ビン」
        - "P" (プラ容器包装): 「容器包装プラスチック」「その他プラ」「プラ」「トレイ」
        - "S" (資源ゴミ・粗大ごみ): 「資源」「粗大ごみ」「粗大」
        - "K" (危険ごみ): 「危険ごみ」「危険」

        【出力JSON仕様】
        1. "towns": 対象行政区（町名）の配列。
        2. "schedule": 月（1〜12）をキーとし、各ゴミ記号（M, N, R, C, B, P, S, K）の収集日の配列を持つオブジェクト。

        【出力例（{area_name}が藪塚本町エリア版の場合の5月の正解データ）】
        {{
            "towns": ["〇〇町", "△△町"],
            "schedule": {{
                "5": {{
                    "M": [4, 7, 11, 14, 18, 21, 25, 28],
                    "R": [1, 19],
                    "B": [8, 22],
                    "C": [15, 29],
                    "N": [6, 20],
                    "K": [14],
                    "P": [12, 26],
                    "S": [13, 27]
                }}
            }}
        }}
        必ず上記と同じJSON構造のみを出力してください。Markdownのコードブロックは不要です。
        """
        response = model.generate_content([uploaded_file, prompt])
        genai.delete_file(uploaded_file.name)
        return json.loads(response.text)
    except Exception as e:
        print(f"[{area_name}] エラー: {e}")
        return None
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

def main():
    os.makedirs(TOWN_DIR, exist_ok=True)
    pdf_links = get_pdf_links()
    if not pdf_links:
        print("エラー: PDFリンクが見つかりません。")
        return

    area_town_map = {}

    for pdf in pdf_links:
        area_name = pdf['area']
        pdf_url = pdf['url']
        data = process_pdf_with_gemini(area_name, pdf_url)
        
        if data and "towns" in data:
            towns = data["towns"]
            area_town_map[area_name] = towns
            
            for town in towns:
                safe_town_name = re.sub(r'[\\/*?:"<>|]', "", town)
                if not safe_town_name: continue
                
                # PDFのURLもJSONに保存し、UI側で参照できるようにする
                town_data = {
                    "metadata": {
                        "area": area_name, 
                        "town": safe_town_name,
                        "pdf_url": pdf_url 
                    },
                    "schedule": data.get("schedule", {})
                }
                
                file_path = os.path.join(TOWN_DIR, f"{safe_town_name}.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(town_data, f, ensure_ascii=False, indent=2)
                    
            print(f"[{area_name}] -> {len(towns)}町名のカレンダーを保存しました。")
        
        time.sleep(10)

    # 画面UIが読み込むための「目次（インデックス）」を作成
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(area_town_map, f, ensure_ascii=False, indent=2)
    print(f"=== 完了: 目次ファイルを {INDEX_FILE} に作成しました ===")

if __name__ == "__main__":
    main()
    
