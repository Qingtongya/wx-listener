import os
import json
import time
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from wxauto import WeChat

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

app = Flask(__name__)
CORS(app)

# 配置文件路径
CONFIG_FILE = 'data/config.json'
NOTIFICATIONS_FILE = 'data/notifications.json'

# 确保数据目录存在
os.makedirs('data', exist_ok=True)

# 硅基流动API配置
SILIGENCE_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILIGENCE_API_KEY = ""
MODEL_NAME = "deepseek-ai/DeepSeek-R1"


class WeChatMonitor:
    def __init__(self):
        self.wx = WeChat()
        self.config = self.load_config()
        self.active_listeners = {}

    def load_config(self):
        """加载配置文件"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            default_config = {
                'target_groups': [],
                'keywords': ['通知', '重要', '紧急', '提醒', '必看'],
                'enable_alert': True
            }
            self.save_config(default_config)
            return default_config

    def save_config(self, config=None):
        """保存配置文件"""
        if config is None:
            config = self.config
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def call_model_api(self, prompt):
        """调用硅基流动API的通用方法"""
        headers = {
            "Authorization": f"Bearer {SILIGENCE_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "enable_thinking": True,
            "thinking_budget": 4096,
            "min_p": 0.05,
            "temperature": 0.7,
            "top_p": 0.7,
            "top_k": 50,
            "frequency_penalty": 0.5,
            "n": 1,
            "stream": False
        }

        try:
            response = requests.post(
                SILIGENCE_API_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"API调用失败: {str(e)}")
            if hasattr(e, 'response') and e.response:
                print(f"错误响应: {e.response.text}")
            return None

    def test_model_connection(self):
        """测试模型连接"""
        test_prompt = "请回答'API连接正常'"
        result = self.call_model_api(test_prompt)

        if result and "choices" in result:
            return {
                "success": True,
                "response": result["choices"][0]["message"]["content"]
            }
        return {
            "success": False,
            "error": "无法获取模型响应"
        }

    def analyze_with_model(self, prompt):
        """使用硅基流动API分析文本"""
        print("使用模型进行分析")
        result = self.call_model_api(prompt)
        if result and "choices" in result:
            return result["choices"][0]["message"]["content"]
        return ""

    def is_notification(self, content):
        """判断消息是否为重要通知"""
        keywords = self.config.get('keywords', [])
        has_keyword = any(keyword in content for keyword in keywords)

        if has_keyword or len(content) > 20:
            prompt = f"请严格判断以下消息是否为重要通知（如活动通知、紧急通知、重要提醒等），只需回答是或否:\n{content}"
            response = self.analyze_with_model(prompt)
            print(response)
            return "是" in response.strip()
        return False

    def extract_notification_info(self, content):
        """提取通知中的关键信息"""
        prompt = f"""请严格从以下通知中提取关键信息，返回规范的JSON格式，确保所有属性名用双引号包围：
        {{
            "title": "通知标题",
            "time": "时间信息",
            "location": "地点信息",
            "content": "主要内容摘要",
            "action": "需要采取的行动",
            "is_urgent": false
        }}

        通知内容：
        {content}"""

        default_info = {
            "title": "未知通知",
            "time": "",
            "location": "",
            "content": content[:200],
            "action": "",
            "is_urgent": False
        }

        try:
            response = self.analyze_with_model(prompt)

            print(json.load(response))

            # 尝试直接解析
            try:
                info = json.loads(response)
            except json.JSONDecodeError:
                # 尝试提取JSON部分
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = response[start:end]
                    info = json.loads(json_str)
                else:
                    raise ValueError("响应中未找到有效JSON")

            # 验证并补全字段
            for key in default_info:
                if key not in info:
                    info[key] = default_info[key]

                # 确保is_urgent是布尔值
                if key == 'is_urgent' and isinstance(info[key], str):
                    info[key] = info[key].lower() in ('true', '是', 'yes')

            return info
        except Exception as e:
            print(f"提取通知信息失败: {e}\n原始响应: {response}")
            return default_info

    def save_notification(self, notification_data):
        """存储通知信息"""
        notification = {
            "id": f"ntf-{int(time.time())}",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            **notification_data
        }

        try:
            with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
                notifications = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            notifications = []

        notifications.append(notification)

        with open(NOTIFICATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(notifications, f, ensure_ascii=False, indent=2)

    def on_message(self, msg, chat):
        """消息处理回调函数"""
        try:
            content = str(getattr(msg, 'content', ''))
            sender = str(getattr(msg, 'sender', '未知'))
            chat_name = str(chat)

            print(f"收到消息 - 群: {chat_name}, 发送者: {sender}, 内容: {content[:100]}...")

            if self.is_notification(content):
                print("检测到重要通知!")
                notification_info = self.extract_notification_info(content)

                self.save_notification({
                    "group": chat_name,
                    "sender": sender,
                    "raw_content": content,
                    **notification_info
                })
        except Exception as e:
            print(f"处理消息时出错: {e}")

    def add_group_listener(self, group_name):
        """添加群聊监听"""
        if group_name not in self.active_listeners:
            self.wx.AddListenChat(nickname=group_name, callback=self.on_message)
            self.active_listeners[group_name] = True

            if group_name not in self.config['target_groups']:
                self.config['target_groups'].append(group_name)
                self.save_config()
            return True
        return False

    def remove_group_listener(self, group_name):
        """移除群聊监听"""
        if group_name in self.active_listeners:
            self.wx.RemoveListenChat(nickname=group_name)
            del self.active_listeners[group_name]

            if group_name in self.config['target_groups']:
                self.config['target_groups'].remove(group_name)
                self.save_config()
            return True
        return False

    def start_monitoring(self):
        """启动监控"""
        # 启动前测试模型连接
        test_result = self.test_model_connection()
        if not test_result['success']:
            print("警告: 模型连接测试失败，通知分析功能可能不可用")

        for group in self.config['target_groups']:
            self.add_group_listener(group)
        self.wx.KeepRunning()

    def save_notification(self, notification_data):
        """存储通知信息"""
        notification = {
            "id": f"ntf-{int(time.time())}",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "is_read": False,  # 新增未读状态
            **notification_data
        }

        try:
            with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
                notifications = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            notifications = []

        notifications.append(notification)

        with open(NOTIFICATIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(notifications, f, ensure_ascii=False, indent=2)

    # 新增方法
    def mark_notification_as_read(self, notification_id):
        """标记通知为已读"""
        try:
            with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
                notifications = json.load(f)

            updated = False
            for note in notifications:
                if note['id'] == notification_id:
                    note['is_read'] = True
                    updated = True

            if updated:
                with open(NOTIFICATIONS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(notifications, f, ensure_ascii=False, indent=2)
                return True
            return False
        except Exception as e:
            print(f"标记已读失败: {e}")
            return False

    # 新增方法
    def delete_notification(self, notification_id):
        """删除通知"""
        try:
            with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
                notifications = json.load(f)

            # 过滤掉要删除的通知
            new_notifications = [n for n in notifications if n['id'] != notification_id]

            if len(new_notifications) < len(notifications):
                with open(NOTIFICATIONS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_notifications, f, ensure_ascii=False, indent=2)
                return True
            return False
        except Exception as e:
            print(f"删除通知失败: {e}")
            return False

# 创建监控实例
monitor = WeChatMonitor()


# API路由
@app.route('/api/groups/monitored', methods=['GET'])
def get_monitored_groups():
    return jsonify(monitor.config.get('target_groups', []))


@app.route('/api/groups/add', methods=['POST'])
def add_monitored_group():
    data = request.json
    if not data or 'group' not in data:
        return jsonify({'error': 'Invalid request'}), 400

    success = monitor.add_group_listener(data['group'])
    return jsonify({'success': success})


@app.route('/api/groups/remove', methods=['POST'])
def remove_monitored_group():
    data = request.json
    if not data or 'group' not in data:
        return jsonify({'error': 'Invalid request'}), 400

    success = monitor.remove_group_listener(data['group'])
    return jsonify({'success': success})


@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    try:
        with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as f:
            notifications = json.load(f)
        return jsonify(notifications)
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify([])


@app.route('/api/model/test', methods=['GET'])
def test_model_api():
    """测试模型API连接"""
    test_result = monitor.test_model_connection()
    if test_result['success']:
        return jsonify({
            "success": True,
            "model": MODEL_NAME,
            "response": test_result['response'],
            "status": "API连接正常"
        })
    else:
        return jsonify({
            "success": False,
            "model": MODEL_NAME,
            "error": test_result.get('error', '未知错误'),
            "status": "API连接失败"
        }), 500


@app.route('/api/start', methods=['POST'])
def start_monitoring():
    import threading
    threading.Thread(target=monitor.start_monitoring, daemon=True).start()
    return jsonify({'success': True})

@app.route('/api/notifications/<notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    success = monitor.mark_notification_as_read(notification_id)
    return jsonify({'success': success})

@app.route('/api/notifications/<notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    success = monitor.delete_notification(notification_id)
    return jsonify({'success': success})

if __name__ == '__main__':
    app.run(debug=True, port=5000)