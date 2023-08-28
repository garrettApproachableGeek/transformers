# coding=utf-8
# Copyright 2023 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Testing suite for the PyTorch CLVP model. """


import copy
import inspect
import os
import tempfile
import unittest

import datasets

import numpy as np

from transformers import CLVPConfig, CLVPSpeechConfig, CLVPTextConfig, CLVPAutoRegressiveConfig, CLVPFeatureExtractor
from transformers.testing_utils import (
    require_torch,
    slow,
    torch_device,
)
from transformers.utils import is_torch_available

from ...test_configuration_common import ConfigTester
from ...test_modeling_common import (
    ModelTesterMixin,
    _config_zero_init,
    ids_tensor,
    random_attention_mask,
)

if is_torch_available():
    import torch
    from torch import nn

    from transformers import (
        CLVPModel,
        CLVPTransformerWithProjection,
    )
    from transformers.models.clvp.modeling_clvp import CLVP_PRETRAINED_MODEL_ARCHIVE_LIST

from transformers import CLVPTokenizer, CLVPFeatureExtractor


class CLVPTransformerWithProjectionTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=7,
        is_training=True,
        use_input_mask=True,
        use_labels=True,
        vocab_size=300,
        hidden_size=32,
        projection_dim=32,
        num_hidden_layers=2,
        num_attention_heads=4,
        intermediate_size=64,
        dropout=0.1,
        attention_dropout=0.1,
        initializer_range=0.02,
        scope=None,
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.use_input_mask = use_input_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.projection_dim = projection_dim
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.intermediate_size = intermediate_size
        self.dropout = dropout
        self.attention_dropout = attention_dropout
        self.initializer_range = initializer_range
        self.scope = scope

    def get_config(self):
        # we are only checking with speech config though both of the configs have same attributes
        speech_config = CLVPSpeechConfig(
            vocab_size=self.vocab_size,
            hidden_size=self.hidden_size,
            projection_dim=self.projection_dim,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            intermediate_size=self.intermediate_size,
            dropout=self.dropout,
            attention_dropout=self.attention_dropout,
            initializer_range=self.initializer_range,
        )

        return speech_config

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.seq_length], self.vocab_size)

        input_mask = None
        if self.use_input_mask:
            input_mask = random_attention_mask([self.batch_size, self.seq_length])

        if input_mask is not None:
            batch_size, seq_length = input_mask.shape
            rnd_start_indices = np.random.randint(1, seq_length - 1, size=(batch_size,))
            for batch_idx, start_index in enumerate(rnd_start_indices):
                input_mask[batch_idx, :start_index] = 1
                input_mask[batch_idx, start_index:] = 0

        speech_config = self.get_config()

        return speech_config, input_ids, input_mask

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        speech_config, input_ids, input_mask = config_and_inputs
        inputs_dict = {"input_ids": input_ids.to(torch_device), "attention_mask": input_mask.to(torch_device)}
        return speech_config, inputs_dict

    def create_and_check_model(self, speech_config, input_ids, input_mask):
        # check the model with both type of inputs
        text_config = CLVPTextConfig(
            vocab_size=self.vocab_size,
            hidden_size=self.hidden_size,
            projection_dim=self.projection_dim,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            intermediate_size=self.intermediate_size,
            dropout=self.dropout,
            attention_dropout=self.attention_dropout,
            initializer_range=self.initializer_range,
        )
        text_model = CLVPTransformerWithProjection(config=text_config)
        text_model.to(torch_device)
        text_model.eval()
        with torch.no_grad():
            result = text_model(input_ids, attention_mask=input_mask)
            result = text_model(input_ids)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))
        self.parent.assertEqual(result.text_embeds.shape, (self.batch_size, self.projection_dim))

        # now check with speech config
        speech_model = CLVPTransformerWithProjection(config=speech_config)
        speech_model.to(torch_device)
        speech_model.eval()
        with torch.no_grad():
            result = speech_model(input_ids, attention_mask=input_mask)
            result = speech_model(input_ids)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))
        self.parent.assertEqual(result.speech_embeds.shape, (self.batch_size, self.projection_dim))


@require_torch
class CLVPTransformerWithProjectionTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (CLVPTransformerWithProjection, ) if is_torch_available() else ()
    test_pruning = False
    test_head_masking = False

    def setUp(self):
        self.model_tester = CLVPTransformerWithProjectionTester(self)
        self.text_config_tester = ConfigTester(self, config_class=CLVPTextConfig, hidden_size=64)
        self.speech_config_tester = ConfigTester(self, config_class=CLVPSpeechConfig, hidden_size=64)

    def test_config(self):
        self.text_config_tester.run_common_tests()
        self.speech_config_tester.run_common_tests()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    @unittest.skip(reason="CLVPTextModelWithProjection does not output loss")
    def test_training(self):
        pass

    @unittest.skip(reason="CLVPTextModelWithProjection does not output loss")
    def test_training_gradient_checkpointing(self):
        pass

    @unittest.skip(reason="CLVP does not use inputs_embeds")
    def test_inputs_embeds(self):
        pass

    @slow
    def test_model_from_pretrained(self):
        for model_name in CLVP_PRETRAINED_MODEL_ARCHIVE_LIST[:1]:
            model = CLVPTransformerWithProjection.from_pretrained(model_name)
            self.assertIsNotNone(model)


class CLVPModelTester:
    def __init__(self, parent, is_training=True):
        self.parent = parent
        self.transformer_projection_model_tester = CLVPTransformerWithProjectionTester(parent)
        self.is_training = is_training

    def get_config(self):
        autoregressive_config = CLVPAutoRegressiveConfig(vocab_size=99,
                                                         max_mel_tokens=256,
                                                         max_text_tokens=256,
                                                         n_embd=32,
                                                         n_layer=2,
                                                         n_head=2,
                                                         bos_token_id=97,
                                                         eos_token_id=98,
                                                         relative_attention_num_buckets=4,
                                                         relative_attention_max_distance=16,
                                                         )

        return CLVPConfig.from_text_speech_autoregressive_configs(
            self.transformer_projection_model_tester.get_config(),# text config
            self.transformer_projection_model_tester.get_config(),# text config used as speech config as they have same attributes
            autoregressive_config, # autoregressive config
            projection_dim=64
        )

    def prepare_config_and_inputs(self):
        _, input_ids, attention_mask = self.transformer_projection_model_tester.prepare_config_and_inputs()

        ds = datasets.load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
        ds = ds.cast_column("audio", datasets.Audio(sampling_rate=22050))
        _, audio, sr = ds.sort("id").select(range(1))[:1]["audio"][0].values()

        feature_extractor = CLVPFeatureExtractor()
        input_features = feature_extractor(raw_speech=audio, sampling_rate=sr, return_tensors="pt")["input_features"].to(torch_device)

        config = self.get_config()

        return config, input_ids, attention_mask, input_features

    def create_and_check_model(self, config, input_ids, attention_mask, input_features):
        model = CLVPModel(config).to(torch_device).eval()
        with torch.no_grad():
            result = model(input_ids=input_ids, input_features=input_features, attention_mask=attention_mask)

        self.parent.assertEqual(
            result.logits_per_speech.shape, (2, self.transformer_projection_model_tester.batch_size)
        )
        self.parent.assertEqual(
            result.logits_per_text.shape, (self.transformer_projection_model_tester.batch_size, 2)
        )

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, input_ids, attention_mask, input_features = config_and_inputs
        inputs_dict = {
            "input_ids": input_ids.to(torch_device),
            "attention_mask": attention_mask.to(torch_device),
            "input_features": input_features.to(torch_device),
            "return_loss": False,
            # "return_dict": True,
        }
        return config, inputs_dict


@require_torch
class CLVPModelTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (CLVPModel,) if is_torch_available() else ()

    test_head_masking = False
    test_pruning = False
    test_resize_embeddings = False
    test_attention_outputs = False
    test_torchscript = False

    def setUp(self):
        self.model_tester = CLVPModelTester(self)

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    @unittest.skip(reason="CLVPModel does not output Hidden_states, since it has two types(text and speech) of them")
    def test_hidden_states_output(self):
        pass

    @unittest.skip(reason="CLVPModel does not take inputs_embeds as inputs")
    def test_inputs_embeds(self):
        pass

    @unittest.skip(reason="Retain_grad is tested in individual model tests")
    def test_retain_grad_hidden_states_attentions(self):
        pass

    @unittest.skip(reason="CLVPModel does not have input/output embeddings, since it has two types(text and speech) of them")
    def test_model_common_attributes(self):
        pass

    # override as the `logit_scale` parameter initilization is different for CLVP
    def test_initialization(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        configs_no_init = _config_zero_init(config)
        for model_class in self.all_model_classes:
            model = model_class(config=configs_no_init)
            for name, param in model.named_parameters():
                if param.requires_grad:
                    # check if `logit_scale` is initilized as per the original implementation
                    if name == "logit_scale":
                        self.assertAlmostEqual(
                            param.data.item(),
                            np.log(1 / 0.07),
                            delta=1e-3,
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )
                    else:
                        self.assertIn(
                            ((param.data.mean() * 1e9).round() / 1e9).item(),
                            [0.0, 1.0],
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )

    def test_load_speech_text_autoregressive_config(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        # Save CLVPConfig and check if we can load CLVPSpeechConfig from it
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            config.save_pretrained(tmp_dir_name)
            speech_config = CLVPSpeechConfig.from_pretrained(tmp_dir_name)
            self.assertDictEqual(config.speech_config.to_dict(), speech_config.to_dict())

        # Save CLVPConfig and check if we can load CLVPTextConfig from it
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            config.save_pretrained(tmp_dir_name)
            text_config = CLVPTextConfig.from_pretrained(tmp_dir_name)
            self.assertDictEqual(config.text_config.to_dict(), text_config.to_dict())

        # Save CLVPConfig and check if we can load CLVPAutoRegressiveConfig from it
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            config.save_pretrained(tmp_dir_name)
            autoregressive_config = CLVPAutoRegressiveConfig.from_pretrained(tmp_dir_name)
            self.assertDictEqual(config.autoregressive_config.to_dict(), autoregressive_config.to_dict())

    @slow
    def test_model_from_pretrained(self):
        for model_name in CLVP_PRETRAINED_MODEL_ARCHIVE_LIST[:1]:
            model = CLVPModel.from_pretrained(model_name)
            self.assertIsNotNone(model)



# Since CLVP has a lot of different models connected with each other it's better to test each of them individually along
# with a test_full_model_integration. If the model breaks in future, it could be of a great help to identify the broken part.

@slow
@require_torch
class CLVPModelIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.text = "This is an example text."
        ds = datasets.load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
        ds = ds.cast_column("audio", datasets.Audio(sampling_rate=22050))
        _, self.speech_samples, self.sr = ds.sort("id").select(range(1))[:1]["audio"][0].values()

        self.model = CLVPModel.from_pretrained("susnato/clvp_dev").to(torch_device)
        self.model.eval()
        tokenizer = CLVPTokenizer.from_pretrained("susnato/clvp_dev")
        feature_extractor = CLVPFeatureExtractor.from_pretrained("susnato/clvp_dev")

        self.text_tokens = tokenizer(self.text, return_tensors="pt")["input_ids"].to(torch_device)
        self.input_features = feature_extractor(raw_speech=self.speech_samples, sampling_rate=self.sr, return_tensors="pt")[
            "input_features"].to(torch_device)

    def test_conditional_encoder(self):
        with torch.no_grad():
            conditioning_encoder_outputs = self.model.conditioning_encoder(mel_spec=self.input_features,
                                 text_tokens=self.text_tokens).to("cpu")

        self.assertEqual(
            conditioning_encoder_outputs.shape,
            torch.Size((self.input_features.shape[0], 18, self.model.config.autoregressive_config.n_embd)),
        )

        EXPECTED_OUTPUTS = torch.tensor([[-0.8582,  0.5228,  1.9944],
        [-0.0465, -1.1017, -0.0093],
        [-0.0466, -0.6030, -0.1280]])

        self.assertTrue(
            torch.allclose(conditioning_encoder_outputs[0, :3, :3], EXPECTED_OUTPUTS, atol=1e-4)
        )

    def test_autoregressive_model_generate(self):
        autoregressive_model_output = self.model.autoregressive_model.generate(input_ids=self.text_tokens).cpu()

        EXPECTED_OUTPUTS = torch.tensor([[ 147,    2,   54,    2,   43,    2,  169,  122,   29,   64,    2,  136,
           37,   33,    9, 8193]])

        self.assertTrue(torch.allclose(autoregressive_model_output, EXPECTED_OUTPUTS))

    def test_speech_and_text_projection_models(self):
        # check for text embeds
        text_embeds = self.model.text_model(input_ids=self.text_tokens).text_embeds.cpu()
        EXPECTED_TEXT_EMBEDS = torch.tensor([  1.4798,  -2.0005,   2.3902,  -0.5042,   1.6401,  -2.4135,  -1.4800,
          3.0118,  -2.4422,   1.3267,   2.2339,   1.4761,  -4.8983,  -1.3592,
          6.0251,   6.7364,   2.2576,   3.7229, -10.0436,   4.6676])
        self.assertTrue(torch.allclose(text_embeds[0, :20], EXPECTED_TEXT_EMBEDS, atol=1e-4))

        # check for speech embeds
        speech_embeds = self.model.speech_model(input_ids=self.text_tokens).speech_embeds.cpu()
        EXPECTED_SPEECH_EMBEDS = torch.tensor([ 3.1202, -3.1183, -1.4264, -6.1339,  1.8885, -0.1983,  0.9461, -1.7414,
         0.3320, -3.8400, -1.5715,  1.5096, -1.7576,  0.2387,  4.9758,  5.8450,
        -6.2534,  2.8586, -5.5816,  4.7821])
        self.assertTrue(torch.allclose(speech_embeds[0, :20], EXPECTED_SPEECH_EMBEDS, atol=1e-4))

    def test_full_model_integration(self):
        full_model_output = self.model.generate(input_ids=self.text_tokens, input_features=self.input_features,
                       do_sample=False,
                       num_beams=4,
                       num_return_sequences=4,
                       max_new_tokens=16,
                       ).speech_candidates.cpu()

        EXPECTED_OUTPUTS = torch.tensor([[ 729,  155,  334],
                                            [ 757,  729, 1305],
                                            [ 729,  757,  334]])


        self.assertTrue(torch.allclose(full_model_output[-3:, -3:], EXPECTED_OUTPUTS))

