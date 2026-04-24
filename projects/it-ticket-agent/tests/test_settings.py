from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch


class SettingsProviderTest(unittest.TestCase):
    def _settings_with_env(self, env: dict[str, str]):
        import it_ticket_agent.settings as settings_module

        keys = {
            "LLM_PROVIDER",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "LLM_MODEL",
            "LLM_WIRE_API",
            "LLM_RICHADO_API_KEY",
            "LLM_YUANGEGE_API_KEY",
        }
        cleared = {key: "" for key in keys}
        with patch.dict(os.environ, {**cleared, **env}, clear=False):
            reloaded = importlib.reload(settings_module)
            settings = reloaded.Settings()
        importlib.reload(settings_module)
        return settings

    def test_default_llm_provider_is_richado_responses(self) -> None:
        settings = self._settings_with_env({"LLM_RICHADO_API_KEY": "richado-key"})

        self.assertEqual(settings.llm_provider, "richado")
        self.assertEqual(settings.llm_base_url, "http://richado.qzz.io:8091")
        self.assertEqual(settings.llm_model, "gpt-5.5")
        self.assertEqual(settings.llm_wire_api, "responses")
        self.assertEqual(settings.llm_api_key, "richado-key")

    def test_previous_alias_selects_yuangege_chat_provider(self) -> None:
        settings = self._settings_with_env(
            {
                "LLM_PROVIDER": "previous",
                "LLM_YUANGEGE_API_KEY": "yuangege-key",
            }
        )

        self.assertEqual(settings.llm_provider, "yuangege")
        self.assertEqual(settings.llm_base_url, "https://api.yuangege.cloud/v1")
        self.assertEqual(settings.llm_model, "gpt-5.5")
        self.assertEqual(settings.llm_wire_api, "chat")
        self.assertEqual(settings.llm_api_key, "yuangege-key")

    def test_explicit_llm_env_overrides_provider_preset(self) -> None:
        settings = self._settings_with_env(
            {
                "LLM_PROVIDER": "richado",
                "LLM_BASE_URL": "https://custom.example/v1",
                "LLM_MODEL": "custom-model",
                "LLM_WIRE_API": "chat",
                "LLM_API_KEY": "global-key",
                "LLM_RICHADO_API_KEY": "richado-key",
            }
        )

        self.assertEqual(settings.llm_base_url, "https://custom.example/v1")
        self.assertEqual(settings.llm_model, "custom-model")
        self.assertEqual(settings.llm_wire_api, "chat")
        self.assertEqual(settings.llm_api_key, "global-key")

    def test_none_provider_disables_llm_without_explicit_overrides(self) -> None:
        settings = self._settings_with_env({"LLM_PROVIDER": "none"})

        self.assertEqual(settings.llm_provider, "none")
        self.assertEqual(settings.llm_base_url, "")
        self.assertEqual(settings.llm_model, "")
        self.assertEqual(settings.llm_api_key, "")
