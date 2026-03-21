# sentiment_engine.py
import json
import os
import re

from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing.text import tokenizer_from_json
from tensorflow.keras.preprocessing.sequence import pad_sequences


class SentimentEngine:
    def __init__(
        self,
        model_path="best_consignment_model.keras",
        tokenizer_path="tokenizer.json",
        config_path="sentiment_config.json",
    ):
        # 파일 존재 확인
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"모델 파일이 없습니다: {model_path}")
        if not os.path.exists(tokenizer_path):
            raise FileNotFoundError(f"tokenizer 파일이 없습니다: {tokenizer_path}")
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"config 파일이 없습니다: {config_path}")

        # 모델 로드
        self.model = load_model(model_path)

        # 토크나이저 로드
        with open(tokenizer_path, "r", encoding="utf-8") as f:
            tok_json = f.read()
        self.tokenizer = tokenizer_from_json(tok_json)

        # 설정 로드
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.MAX_LEN = int(cfg.get("MAX_LEN", 10))

        # 불용어(너가 쓰던 것 그대로)
        self.stopwords = set([
            # 조사
            '이', '가', '을', '를', '의', '에', '에서', '로', '으로', '와', '과',
            '도', '만', '까지', '부터', '한테', '에게', '께', '더러', '라고',

            # 어미
            '은', '는', '이다', '입니다', '습니다', 'ㅂ니다', '합니다',
            '해요', '이에요', '예요', '네요', '군요', '구나', '구먼',

            # 대명사
            '저', '제', '나', '내', '우리', '저희', '너', '당신',
            '이것', '그것', '저것', '여기', '거기', '저기',

            # 관형사/부사 (의미 없는 것만)
            '그', '이', '저', '어떤', '무슨', '모든', '어느',
            '좀', '잘', '더', '덜', '매우', '아주', '조금', '많이',

            # 접속사
            '그리고', '그러나', '하지만', '또', '및', '또는', '혹은',

            # 기타 불필요한 단어
            '것', '수', '등', '및', '때', '년', '월', '일',
            '하다', '되다', '있다', '없다', '아니다',

            # 탁송 리뷰에서 의미 없는 단어들
            '이용', '서비스', '업체', '회사'
        ])

        # ✅ Okt는 있으면 쓰고, Java 없으면 None으로 두고 fallback
        self.okt = None
        try:
            from konlpy.tag import Okt
            self.okt = Okt()  # JVM 없으면 여기서 예외가 날 수 있음
        except Exception:
            self.okt = None

    def tokenize(self, text: str):
        text = (text or "").strip()
        if not text:
            return []

        if self.okt is not None:
            tokens = self.okt.morphs(text, stem=True)
        else:
            # ✅ Java 없을 때: 특수문자 제거 후 공백 분리(간단 토큰화)
            tokens = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text).split()

        tokens = [t for t in tokens if t and (t not in self.stopwords)]
        return tokens

    # ✅ app.py가 호출하는 메서드 이름: predict()
    def predict(self, text: str):
        text = (text or "").strip()
        if not text:
            return None, "text(리뷰 문장)를 입력하세요."

        tokens = self.tokenize(text)
        if not tokens:
            return None, "분석 가능한 토큰이 없습니다. 다른 문장으로 시도해 주세요."

        seq = self.tokenizer.texts_to_sequences([tokens])
        padded = pad_sequences(seq, maxlen=self.MAX_LEN)

        prob = float(self.model.predict(padded, verbose=0)[0][0])
        label = "긍정" if prob >= 0.5 else "부정"

        # ✅ 여기엔 numpy 배열 같은 거 넣지 말기
        return {
            "prob": round(prob, 4),
            "label": label,
            "tokens": tokens,
            "mode": "simple"
        }, None
