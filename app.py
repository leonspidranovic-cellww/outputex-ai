import os
import re
import json
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template
from duckduckgo_search import DDGS
from gigachat import GigaChat

app = Flask(__name__)

# =======================================================
# КЛЮЧИ
# =======================================================
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
if not GIGACHAT_CREDENTIALS:
    raise ValueError("GIGACHAT_CREDENTIALS не установлена! Добавьте её в переменные окружения.")
GIGACHAT_SCOPE = "GIGACHAT_API_PERS"
    
# ===== ЧЁРНЫЙ СПИСОК ДОМЕНОВ =====
BLACKLIST_DOMAINS = [
    'facebook.com', 'support.mozilla.org', 'answers.com',
    'vk.com', 'instagram.com', 'twitter.com',
    'zhihu.com', 'who.int', 'forum.coronarenderer.com'
]

def is_bad_domain(url):
    for domain in BLACKLIST_DOMAINS:
        if domain in url.lower():
            return True
    return False

# ===== Очистка запроса =====
def clean_query(query):
    patterns_greeting = [
        r'^(привет|здравствуй|здравствуйте|добрый день|добрый вечер|доброе утро|салют|хай|hello|hi)\s*,?\s*',
        r'^(скажи|расскажи|напиши|ответь|помоги|подскажи|объясни|покажи)\s*,?\s*'
    ]
    for pat in patterns_greeting:
        query = re.sub(pat, '', query, flags=re.IGNORECASE)

    patterns_question = [
        r'^(какой|какая|какое|какие|какого|какой-то|что|кто|чьи|чей|сколько|почему|зачем|где|куда|откуда|когда)\s+'
    ]
    for pat in patterns_question:
        query = re.sub(pat, '', query, flags=re.IGNORECASE)

    query = re.sub(r'^[,.\s]+|[,.\s]+$', '', query)
    return query.strip()

def build_search_query(original_question, cleaned):
    if not cleaned:
        return original_question
    stopwords = {'самый', 'самая', 'самое', 'самые', 'очень', 'слишком', 'весьма'}
    words = cleaned.split()
    filtered = [w for w in words if w.lower() not in stopwords]
    result = ' '.join(filtered)
    plant_keywords = {'арбуз', 'вишня', 'яблоко', 'клубника', 'малина', 'сорт', 'сорта'}
    if any(p in original_question.lower() for p in plant_keywords):
        if 'сорт' not in result.lower():
            result = result + ' сорт'
    return result if result else original_question

def search_duckduckgo(query, max_results=10):
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results, region='ru-ru'):
                url = r.get('href', '')
                if is_bad_domain(url):
                    continue
                results.append({
                    "title": r.get("title", "Без заголовка"),
                    "body": r.get("body", ""),
                    "href": url
                })
    except Exception as e:
        print(f"Ошибка DuckDuckGo: {e}")
    return results

def search_google(query, max_results=10):
    results = []
    try:
        from googlesearch import search
        urls = list(search(query, num_results=max_results*2, lang='ru', stop=max_results*2))
        for url in urls:
            if is_bad_domain(url):
                continue
            try:
                response = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    title = soup.title.string.strip() if soup.title and soup.title.string else url
                    meta_desc = soup.find('meta', attrs={'name': 'description'})
                    description = meta_desc.get('content', '').strip() if meta_desc else ''
                    if not description:
                        p = soup.find('p')
                        description = p.get_text().strip()[:300] if p else ''
                    results.append({
                        "title": title[:100],
                        "body": description[:500] if description else "Ссылка: " + url,
                        "href": url
                    })
            except Exception as e:
                print(f"Ошибка загрузки {url}: {e}")
                results.append({
                    "title": url,
                    "body": "Ссылка: " + url,
                    "href": url
                })
            if len(results) >= max_results:
                break
    except Exception as e:
        print(f"Ошибка Google: {e}")
    return results

def search_sources(original_question, max_results=10):
    cleaned = clean_query(original_question)
    if not cleaned:
        cleaned = original_question
    search_query = build_search_query(original_question, cleaned)
    print(f"[DEBUG] Поисковый запрос: {search_query}")

    results = search_duckduckgo(search_query, max_results)
    if len(results) < 5:
        print("[DEBUG] Мало результатов, пробуем Google...")
        google_results = search_google(search_query, max_results)
        existing_urls = {r['href'] for r in results}
        for r in google_results:
            if r['href'] not in existing_urls:
                results.append(r)
                existing_urls.add(r['href'])
    return results[:max_results]

def is_simple_question(text):
    """Определяет, является ли вопрос простым (коротким или приветствием)"""
    text = text.lower().strip()
    simple_phrases = ['привет', 'здравствуй', 'добрый день', 'как дела', 'кто ты', 'что ты умеешь', 'пока']
    if any(p in text for p in simple_phrases):
        return True
    # Если меньше 5 слов – считаем простым
    if len(text.split()) <= 5:
        return True
    return False

def generate_ai_answer(question, sources, deep=False):
    """Генерирует ответ с учётом источников и глубины мышления"""
    # Если вопрос простой – отвечаем кратко без источников
    if is_simple_question(question):
        try:
            with GigaChat(
                credentials=GIGACHAT_CREDENTIALS,
                scope=GIGACHAT_SCOPE,
                model="GigaChat-3-Ultra",
                verify_ssl_certs=False,
                base_url="https://api.giga.chat/v1"
            ) as client:
                response = client.chat.create(question)
                return response.messages[0].content[0].text
        except Exception as e:
            return f"Привет! Я OUTPUTEX AI. Чем могу помочь? (Ошибка: {e})"

    # Если нет источников, но вопрос не простой – даём общий ответ
    if not sources:
        try:
            with GigaChat(
                credentials=GIGACHAT_CREDENTIALS,
                scope=GIGACHAT_SCOPE,
                model="GigaChat-3-Ultra",
                verify_ssl_certs=False,
                base_url="https://api.giga.chat/v1"
            ) as client:
                response = client.chat.create(question)
                return response.messages[0].content[0].text
        except Exception as e:
            return f"Не нашёл информации, но давай подумаем вместе! 😊 (Ошибка: {e})"

    # Формируем контекст из источников
    context_parts = []
    for i, s in enumerate(sources, 1):
        text = s['body']
        if len(text) > 400:
            text = text[:400] + "..."
        context_parts.append(f"Источник {i}: {s['title']}\n{text}\nСсылка: {s['href']}")
    context = "\n\n".join(context_parts)

    # Выбираем стиль в зависимости от deep
    if deep:
        style_instruction = (
            "Дай глубокий, аналитический ответ. Используй структуры: таблицы, списки, сравнения. "
            "Покажи разные точки зрения."
        )
    else:
        style_instruction = (
            "Ответь развёрнуто, но по делу. Используй списки и выделения для ключевых пунктов."
        )

    prompt = f"""
Ты — OUTPUTEX AI, живой собеседник и эксперт. Твоя задача — помогать пользователю наглядно: командами, кодом, цитатами, таблицами, списками.

**ВАЖНО: используй Markdown-разметку:**
- Для **команд** и **кода** – блоки с ```bash, ```python, ```cmd.
- Для **цитат** – `>` в начале строки.
- Для **списков** – `-` или `1.`, `2.`.
- Для **таблиц** – Markdown-синтаксис.
- Для **выделения** – `**жирный**` или `*курсив*`.

{style_instruction}

Вопрос пользователя: {question}

Информация из интернета (используй её, перескажи и дополни структурами):
{context}

Ответь так, чтобы было удобно читать: с заголовками, блоками кода, списками и выделениями.
"""

    try:
        with GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            model="GigaChat-3-Ultra",
            verify_ssl_certs=False,
            base_url="https://api.giga.chat/v1"
        ) as client:
            response = client.chat.create(prompt)
            return response.messages[0].content[0].text
    except Exception as e:
        return f"Ой, что-то пошло не так: {e}"

# =======================================================
# МАРШРУТЫ
# =======================================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json()
    question = data.get('message', '').strip()
    search_enabled = data.get('search', False)
    deep_enabled = data.get('deep', False)

    if not question:
        return jsonify({"error": "Введите вопрос"}), 400

    # Если поиск включён – ищем источники, иначе – пустой список
    sources = []
    if search_enabled:
        sources = search_sources(question, max_results=10)

    answer_text = generate_ai_answer(question, sources, deep_enabled)

    # Формируем ответ для фронта
    source_list = []
    for s in sources:
        if s.get('href') and s['href'] != '#':
            source_list.append({"title": s.get('title', 'Источник'), "url": s['href']})

    return jsonify({
        "reply": answer_text,
        "sources": source_list
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
