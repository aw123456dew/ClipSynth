from sqlalchemy.orm import Session

from clip_synth.core.database import DatabaseManager
from clip_synth.models.settings import AIModelSettings, AppSettings, DoubaoVoiceSettings, TencentAsrSettings, SettingsModel


class SettingsService:
    def __init__(self, db_manager: DatabaseManager):
        self._db_manager = db_manager

    def load(self) -> AppSettings:
        session: Session = self._db_manager.create_session()
        try:
            row = session.get(SettingsModel, 1)
            if row is None:
                return AppSettings()
            return AppSettings(
                text_model=AIModelSettings(
                    model_name=row.text_model_name,
                    api_key=row.text_api_key,
                    base_url=row.text_base_url,
                ),
                vision_model=AIModelSettings(
                    model_name=row.vision_model_name,
                    api_key=row.vision_api_key,
                    base_url=row.vision_base_url,
                ),
                draft_output_dir=row.draft_output_dir,
                doubao_voice=DoubaoVoiceSettings(
                    access_key=row.doubao_access_key,
                    secret_key=row.doubao_secret_key,
                    app_id=row.doubao_app_id,
                    token=row.doubao_token,
                ),
                tencent_asr=TencentAsrSettings(
                    secret_id=row.tencent_asr_secret_id or "",
                    secret_key=row.tencent_asr_secret_key or "",
                    region=row.tencent_asr_region or "ap-guangzhou",
                ),
                image_model=AIModelSettings(
                    model_name=row.image_model_name or "",
                    api_key=row.image_api_key or "",
                    base_url=row.image_base_url or "",
                    api_type=row.image_api_type or "openai",
                ),
                asr_provider=row.asr_provider or "volcengine",
                tts_params=row.tts_params or "",
                gpu_accel_enabled=bool(row.gpu_accel_enabled),
            )
        finally:
            session.close()

    def save(self, settings: AppSettings) -> None:
        session: Session = self._db_manager.create_session()
        try:
            row = session.get(SettingsModel, 1)
            if row is None:
                row = SettingsModel(id=1)
                session.add(row)

            row.text_model_name = settings.text_model.model_name
            row.text_api_key = settings.text_model.api_key
            row.text_base_url = settings.text_model.base_url
            row.vision_model_name = settings.vision_model.model_name
            row.vision_api_key = settings.vision_model.api_key
            row.vision_base_url = settings.vision_model.base_url
            row.draft_output_dir = settings.draft_output_dir
            row.doubao_access_key = settings.doubao_voice.access_key
            row.doubao_secret_key = settings.doubao_voice.secret_key
            row.doubao_app_id = settings.doubao_voice.app_id
            row.doubao_token = settings.doubao_voice.token
            row.tencent_asr_secret_id = settings.tencent_asr.secret_id
            row.tencent_asr_secret_key = settings.tencent_asr.secret_key
            row.tencent_asr_region = settings.tencent_asr.region
            row.image_model_name = settings.image_model.model_name
            row.image_api_key = settings.image_model.api_key
            row.image_base_url = settings.image_model.base_url
            row.image_api_type = settings.image_model.api_type
            row.asr_provider = settings.asr_provider
            row.tts_params = settings.tts_params
            row.gpu_accel_enabled = settings.gpu_accel_enabled

            session.commit()
        finally:
            session.close()
