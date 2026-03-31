"""CS AI 에이전트 서버 실행"""
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "src.webhook_handler:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
