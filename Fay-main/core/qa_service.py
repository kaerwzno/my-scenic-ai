import os
import csv
import difflib
import random
from utils import config_util as cfg
from scheduler.thread_manager import MyThread
import shlex
import subprocess
import time
from utils import util

class QAService:

    def __init__(self):
        pass

    def question(self, query_type, text):
        if query_type == 'qa':
            answer_dict = self.__read_qna(cfg.config['interact'].get('QnA'))
            answer, action = self.__get_keyword(answer_dict, text, query_type)
            if action:
                MyThread(target=self.__run, args=[action]).start()
            return answer, 'qa'
        return None, None

    def __run(self, action):
        time.sleep(0.1)
        args = shlex.split(action)  # 分割命令行参数
        subprocess.Popen(args)

    def __read_qna(self, filename):
        qna = []
        try:
            with open(filename, 'r', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                next(reader)  # 跳过表头
                for row in reader:
                    if len(row) >= 2:
                        qna.append([row[0].split(";"), row[1], row[2] if len(row) >= 3 else None])
        except Exception as e:
            pass
        return qna

    def record_qapair(self, question, answer):
        if not cfg.config['interact']['QnA'] or cfg.config['interact']['QnA'][-3:] != 'csv':
            util.log(1, 'qa文件没有指定，不记录大模型回复')
            return
        log_file = cfg.config['interact']['QnA']  # 指定 CSV 文件的名称或路径
        file_exists = os.path.isfile(log_file)
        with open(log_file, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            if not file_exists:
                # 写入表头
                writer.writerow(['Question', 'Answer'])
            writer.writerow([question, answer])

    def remove_qapair(self, answer):
        """从QA文件中删除指定答案的记录"""
        if not cfg.config['interact']['QnA'] or cfg.config['interact']['QnA'][-3:] != 'csv':
            util.log(1, 'qa文件没有指定')
            return False
        log_file = cfg.config['interact']['QnA']
        if not os.path.isfile(log_file):
            util.log(1, 'qa文件不存在')
            return False

        try:
            # 读取所有记录
            rows = []
            with open(log_file, 'r', encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile)
                rows = list(reader)

            if len(rows) <= 1:
                return False

            # 过滤掉匹配答案的记录（保留表头）
            # 规范化答案：去掉换行符和首尾空格后比较
            header = rows[0]
            filtered_rows = [header]
            removed_count = 0
            answer_normalized = answer.replace('\n', '').replace('\r', '').strip()
            for row in rows[1:]:
                if len(row) >= 2:
                    row_answer_normalized = row[1].replace('\n', '').replace('\r', '').strip()
                    if row_answer_normalized == answer_normalized:
                        removed_count += 1
                    else:
                        filtered_rows.append(row)
                else:
                    filtered_rows.append(row)

            if removed_count > 0:
                # 写回文件
                with open(log_file, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerows(filtered_rows)
                util.log(1, f'从QA文件中删除了 {removed_count} 条记录')
                return True
            else:
                util.log(1, '未找到匹配的QA记录')
                return False
        except Exception as e:
            util.log(1, f'删除QA记录时出错: {e}')
            return False

    def __get_keyword(self, keyword_dict, text, query_type):
        threshold = 0.6
        candidates = []

        for qa in keyword_dict:
            if len(qa) < 2:
                continue
            for quest in qa[0]:
                similar = self.__string_similar(text, quest)
                if quest in text:
                    similar += 0.3
                if similar >= threshold:
                    action = qa[2] if (query_type == "qa" and len(qa) > 2) else None
                    candidates.append((similar, qa[1], action))

        if not candidates:
            return None, None

        # 从所有超过阈值的候选项中随机选择一个
        chosen = random.choice(candidates)
        return chosen[1], chosen[2]

    def __string_similar(self, s1, s2):
        return difflib.SequenceMatcher(None, s1, s2).quick_ratio()


