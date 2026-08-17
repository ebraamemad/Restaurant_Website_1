'''
ده ممكن نأجله شوية.

بس ببساطة.

هو بيقول

لو حد طلب

Settings

100 مرة.

متعملهمش 100 مرة.

اعملهم مرة.

وخزنهم.

وده أسرع.
اهمية 
from functools import lru_cache



'''

from functools import lru_cache

'''
وده أجمل حاجة في FastAPI.

بدل ما تكتب

os.getenv(...)

كل شوية.

هو بيقرأ

.env

لوحده.

from pydantic_settings import BaseSettings, SettingsConfigDict

'''
from pydantic_settings import BaseSettings, SettingsConfigDict

#ده مسؤول عن قراءة الإعدادات من ملف .env.

#بيقول للتطبيق: “اقرأ الإعدادات دي من البيئة”.
#وده معناهأنا هعملClassاسمهSettingsكل اللي فيههييجي من .env
class Settings(BaseSettings):


    

    app_name: str = "Fresh Store"
    app_env: str = "development" # هل المشروعD evelopment or اProduction.
    app_secret_key: str# مفتاج تشفير البيانات زي كلمات السر.
    database_url: str# وده رابط PostgreSQL.
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    cookie_secure: bool = True # Trueيبقى الكوكيزتشتغلHTTPSبس.في التطويرFalseفي الإنتاجTrue.بعدها
    access_token_expire_minutes: int = 60 * 24

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    # هنا بيقول للتطبيق: “اقرأ الإعدادات دي من ملف .env، و لو فيه أي حاجة زيادة عن اللي موجودة في الكلاس، تجاهلها”.



@lru_cache # اعمل caching للإعدادات. يعني لو حد طلب الإعدادات أكتر من مرة، متعملش إعادة قراءة من .env كل مرة، خزنها وارجعها.
def get_settings() -> Settings:
    return Settings()

#دي بتخليك تستخدم الإعدادات في أي ملف:
settings = get_settings()


