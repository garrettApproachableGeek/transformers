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
""" Testing suite for the PyTorch Clvp model. """


import copy
import gc
import tempfile
import unittest

import datasets
import numpy as np

from transformers import AdaptiveEmbedding, ClvpConfig, ClvpDecoderConfig, ClvpEncoderConfig
from transformers.testing_utils import (
    require_torch,
    slow,
    torch_device,
)
from transformers.utils import is_torch_available

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import (
    ModelTesterMixin,
    _config_zero_init,
    ids_tensor,
    random_attention_mask,
)


if is_torch_available():
    import torch

    from transformers import ClvpEncoder, ClvpForCausalLM, ClvpModel, ClvpModelForConditionalGeneration
    from transformers.models.clvp.modeling_clvp import CLVP_PRETRAINED_MODEL_ARCHIVE_LIST

from transformers import ClvpFeatureExtractor, ClvpTokenizer


class ClvpEncoderTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=7,
        is_training=True,
        use_input_mask=True,
        use_labels=True,
        vocab_size=99,
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
        self.bos_token_id = vocab_size - 1
        self.eos_token_id = vocab_size - 1

    def get_config(self):
        encoder_config = ClvpEncoderConfig(
            vocab_size=self.vocab_size,
            hidden_size=self.hidden_size,
            projection_dim=self.projection_dim,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            intermediate_size=self.intermediate_size,
            dropout=self.dropout,
            attention_dropout=self.attention_dropout,
            initializer_range=self.initializer_range,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
        )

        return encoder_config

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

        encoder_config = self.get_config()

        return encoder_config, input_ids, input_mask

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        speech_config, input_ids, input_mask = config_and_inputs
        inputs_dict = {"input_ids": input_ids.to(torch_device), "attention_mask": input_mask.to(torch_device)}
        return speech_config, inputs_dict

    def create_and_check_model(self, speech_config, input_ids, input_mask):
        text_config = ClvpEncoderConfig(
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
        text_encoder_model = ClvpEncoder(config=text_config)
        text_encoder_model.to(torch_device)
        text_encoder_model.eval()
        with torch.no_grad():
            result = text_encoder_model(input_ids, attention_mask=input_mask)
            result = text_encoder_model(input_ids)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))
        self.parent.assertEqual(result[0].shape, (self.batch_size, self.projection_dim))

        # now check with speech config
        speech_encoder_model = ClvpEncoder(config=speech_config)
        speech_encoder_model.to(torch_device)
        speech_encoder_model.eval()
        with torch.no_grad():
            result = speech_encoder_model(input_ids, attention_mask=input_mask)
            result = speech_encoder_model(input_ids)
        self.parent.assertEqual(result.last_hidden_state.shape, (self.batch_size, self.seq_length, self.hidden_size))
        self.parent.assertEqual(result[0].shape, (self.batch_size, self.projection_dim))


@require_torch
class ClvpEncoderTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (ClvpEncoder,) if is_torch_available() else ()
    test_pruning = False
    test_head_masking = False
    test_torchscript = False

    def setUp(self):
        self.model_tester = ClvpEncoderTester(self)
        self.encoder_config_tester = ConfigTester(self, config_class=ClvpEncoderConfig, hidden_size=64)

    def tearDown(self):
        super().tearDown()
        # clean-up as much as possible GPU memory occupied by PyTorch
        gc.collect()
        torch.cuda.empty_cache()

    def test_config(self):
        self.encoder_config_tester.run_common_tests()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    @unittest.skip(reason="ClvpEncoder does not output loss")
    def test_training(self):
        pass

    @unittest.skip(reason="ClvpEncoder does not output loss")
    def test_training_gradient_checkpointing(self):
        pass


class ClvpDecoderTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=7,
        is_training=True,
        vocab_size=300,
        max_mel_tokens=256,
        max_text_tokens=256,
        use_input_mask=True,
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        bos_token_id=97,
        eos_token_id=98,
        relative_attention_num_buckets=4,
        relative_attention_max_distance=16,
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.vocab_size = vocab_size
        self.max_mel_tokens = max_mel_tokens
        self.max_text_tokens = max_text_tokens
        self.use_input_mask = use_input_mask
        self.hidden_size = hidden_size
        self.num_attention_heads = num_attention_heads
        self.num_hidden_layers = num_hidden_layers
        self.bos_token_id = bos_token_id
        self.eos_token_id = eos_token_id
        self.relative_attention_num_buckets = relative_attention_num_buckets
        self.relative_attention_max_distance = relative_attention_max_distance

    def get_config(self):
        decoder_config = ClvpDecoderConfig(
            vocab_size=self.vocab_size,
            max_mel_tokens=self.max_mel_tokens,
            max_text_tokens=self.max_text_tokens,
            n_embd=self.hidden_size,
            n_layer=self.num_hidden_layers,
            n_head=self.num_attention_heads,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            relative_attention_num_buckets=self.relative_attention_num_buckets,
            relative_attention_max_distance=self.relative_attention_max_distance,
        )

        return decoder_config

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

        decoder_config = self.get_config()

        return decoder_config, input_ids, input_mask

    def create_and_check_model(self, config, input_ids, attention_mask):
        model = ClvpForCausalLM(config).to(torch_device).eval()
        with torch.no_grad():
            result = model(input_ids=input_ids, attention_mask=attention_mask)

        self.parent.assertEqual(result[0].shape, (self.batch_size, self.seq_length, self.vocab_size))

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, input_ids, attention_mask = config_and_inputs
        inputs_dict = {
            "input_ids": input_ids.to(torch_device),
            "attention_mask": attention_mask.to(torch_device),
        }
        return config, inputs_dict


@require_torch
class ClvpDecoderTest(ModelTesterMixin, GenerationTesterMixin, unittest.TestCase):
    all_model_classes = (ClvpModel, ClvpForCausalLM) if is_torch_available() else ()
    all_generative_model_classes = (ClvpForCausalLM,) if is_torch_available() else ()

    test_pruning = False

    def setUp(self):
        self.model_tester = ClvpDecoderTester(self)

    def tearDown(self):
        super().tearDown()
        # clean-up as much as possible GPU memory occupied by PyTorch
        gc.collect()
        torch.cuda.empty_cache()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def _prepare_for_class(self, inputs_dict, model_class, return_labels=False):
        if return_labels and model_class == ClvpForCausalLM:
            inputs_dict["labels"] = torch.zeros(
                [self.model_tester.batch_size, self.model_tester.seq_length], device=torch_device
            ).long()

        return inputs_dict

    def test_training(self):
        # we will only test the ClvpForCausalLM since it outputs loss
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.return_dict = True

        model = ClvpForCausalLM(config)
        model.to(torch_device)
        model.train()
        inputs = self._prepare_for_class(inputs_dict, ClvpForCausalLM, return_labels=True)
        loss = model(**inputs).loss
        loss.backward()

    def test_training_gradient_checkpointing(self):
        # we will only test the ClvpForCausalLM since it outputs loss
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        config.use_cache = False
        config.return_dict = True

        model = ClvpForCausalLM(config)
        model.to(torch_device)
        model.gradient_checkpointing_enable()
        model.train()
        inputs = self._prepare_for_class(inputs_dict, ClvpForCausalLM, return_labels=True)

        loss = model(**inputs).loss
        loss.backward()


class ClvpModelForConditionalGenerationTester:
    def __init__(self, parent, is_training=False):
        self.parent = parent
        self.clvp_encoder_tester = ClvpEncoderTester(parent)
        self.is_training = is_training

    def get_config(self):
        decoder_config = ClvpDecoderConfig(
            vocab_size=200,
            max_mel_tokens=150,
            max_text_tokens=100,
            n_embd=32,
            n_layer=2,
            n_head=2,
            bos_token_id=97,
            eos_token_id=98,
            relative_attention_num_buckets=4,
            relative_attention_max_distance=16,
        )
        text_config = self.clvp_encoder_tester.get_config()
        speech_config = self.clvp_encoder_tester.get_config()
        speech_config.vocab_size = 300

        return ClvpConfig.from_sub_model_configs(
            text_config,
            speech_config,
            decoder_config,
            projection_dim=64,
        )

    def prepare_config_and_inputs(self):
        _, input_ids, attention_mask = self.clvp_encoder_tester.prepare_config_and_inputs()

        ds = datasets.load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
        ds = ds.cast_column("audio", datasets.Audio(sampling_rate=22050))
        _, audio, sr = ds.sort("id").select(range(1))[:1]["audio"][0].values()

        feature_extractor = ClvpFeatureExtractor()
        input_features = feature_extractor(raw_speech=audio, sampling_rate=sr, return_tensors="pt")[
            "input_features"
        ].to(torch_device)

        config = self.get_config()

        return config, input_ids, attention_mask, input_features

    def create_and_check_model(self, config, input_ids, attention_mask, input_features):
        model = ClvpModelForConditionalGeneration(config).to(torch_device).eval()
        with torch.no_grad():
            result = model(input_ids=input_ids, input_features=input_features, attention_mask=attention_mask)

        self.parent.assertEqual(result.logits_per_speech.shape, (2, self.clvp_encoder_tester.batch_size))
        self.parent.assertEqual(result.logits_per_text.shape, (self.clvp_encoder_tester.batch_size, 2))

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        config, input_ids, attention_mask, input_features = config_and_inputs
        inputs_dict = {
            "input_ids": input_ids.to(torch_device),
            "attention_mask": attention_mask.to(torch_device),
            "input_features": input_features.to(torch_device),
            "return_loss": False,
        }
        return config, inputs_dict


@require_torch
class ClvpModelForConditionalGenerationTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (ClvpModelForConditionalGeneration,) if is_torch_available() else ()

    test_head_masking = False
    test_pruning = False
    test_resize_embeddings = False
    test_attention_outputs = False
    test_torchscript = False

    def setUp(self):
        self.model_tester = ClvpModelForConditionalGenerationTester(self)

    def tearDown(self):
        super().tearDown()
        # clean-up as much as possible GPU memory occupied by PyTorch
        gc.collect()
        torch.cuda.empty_cache()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    @unittest.skip(
        reason="ClvpModelForConditionalGeneration does not output Hidden_states, since it has two types(text and speech) of them"
    )
    def test_hidden_states_output(self):
        pass

    @unittest.skip(reason="Retain_grad is tested in individual model tests")
    def test_retain_grad_hidden_states_attentions(self):
        pass

    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        model = ClvpModelForConditionalGeneration(config)
        model.to(torch_device)
        model.eval()

        inputs = copy.deepcopy(self._prepare_for_class(inputs_dict, ClvpModelForConditionalGeneration))

        input_ids = inputs["input_ids"]
        del inputs["input_ids"]

        conditioning_encoder_wte = model.get_conditioning_encoder_input_embeddings()
        text_encoder_wte = model.get_text_encoder_input_embeddings()

        inputs["conditioning_encoder_inputs_embeds"] = conditioning_encoder_wte(input_ids)
        inputs["text_encoder_inputs_embeds"] = text_encoder_wte(input_ids)

        with torch.no_grad():
            model(**inputs)[0]

    def test_model_common_attributes(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        model = ClvpModelForConditionalGeneration(config)
        self.assertIsInstance(
            model.get_conditioning_encoder_input_embeddings(), (torch.nn.Embedding, AdaptiveEmbedding)
        )
        self.assertIsInstance(model.get_text_encoder_input_embeddings(), (torch.nn.Embedding, AdaptiveEmbedding))
        self.assertIsInstance(model.get_speech_encoder_input_embeddings(), (torch.nn.Embedding, AdaptiveEmbedding))

        model.set_conditioning_encoder_input_embeddings(torch.nn.Embedding(10, 10))
        model.set_text_encoder_input_embeddings(torch.nn.Embedding(10, 10))
        model.set_speech_encoder_input_embeddings(torch.nn.Embedding(10, 10))

    # override as the `logit_scale` parameter initilization is different for Clvp
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

    def test_load_speech_text_decoder_config(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        # Save ClvpConfig and check if we can load ClvpEncoderConfig from it
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            config.save_pretrained(tmp_dir_name)
            encoder_config = ClvpEncoderConfig.from_pretrained(tmp_dir_name)
            self.assertDictEqual(config.text_config.to_dict(), encoder_config.to_dict())

        # Save ClvpConfig and check if we can load ClvpDecoderConfig from it
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            config.save_pretrained(tmp_dir_name)
            decoder_config = ClvpDecoderConfig.from_pretrained(tmp_dir_name)
            self.assertDictEqual(config.decoder_config.to_dict(), decoder_config.to_dict())

    @slow
    def test_model_from_pretrained(self):
        for model_name in CLVP_PRETRAINED_MODEL_ARCHIVE_LIST[:1]:
            model = ClvpModelForConditionalGeneration.from_pretrained(model_name)
            self.assertIsNotNone(model)


# Since Clvp has a lot of different models connected with each other it's better to test each of them individually along
# with a test_full_model_integration. If the model breaks in future, it could be of a great help to identify the broken part.


@slow
@require_torch
class ClvpIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.text = "This is an example text."
        ds = datasets.load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
        ds = ds.cast_column("audio", datasets.Audio(sampling_rate=22050))
        _, self.speech_samples, self.sr = ds.sort("id").select(range(1))[:1]["audio"][0].values()

        self.model = ClvpModelForConditionalGeneration.from_pretrained("susnato/clvp_dev").to(torch_device)
        self.model.eval()
        tokenizer = ClvpTokenizer.from_pretrained("susnato/clvp_dev")
        feature_extractor = ClvpFeatureExtractor.from_pretrained("susnato/clvp_dev")

        self.text_tokens = tokenizer(self.text, return_tensors="pt")["input_ids"].to(torch_device)
        self.input_features = feature_extractor(
            raw_speech=self.speech_samples, sampling_rate=self.sr, return_tensors="pt"
        )["input_features"].to(torch_device)

    def tearDown(self):
        super().tearDown()
        # clean-up as much as possible GPU memory occupied by PyTorch
        gc.collect()
        torch.cuda.empty_cache()

    def test_conditional_encoder(self):
        with torch.no_grad():
            conditioning_encoder_outputs = self.model.conditioning_encoder(
                mel_spec=self.input_features, input_ids=self.text_tokens
            ).to("cpu")

        self.assertEqual(
            conditioning_encoder_outputs.shape,
            torch.Size((self.input_features.shape[0], 18, self.model.config.decoder_config.n_embd)),
        )

        EXPECTED_OUTPUTS = torch.tensor(
            [[-0.8582, 0.5228, 1.9944], [-0.0465, -1.1017, -0.0093], [-0.0466, -0.6030, -0.1280]]
        )

        self.assertTrue(torch.allclose(conditioning_encoder_outputs[0, :3, :3], EXPECTED_OUTPUTS, atol=1e-4))

    def test_decoder_model_generate(self):
        autoregressive_model_output = self.model.speech_decoder_model.generate(input_ids=self.text_tokens).cpu()

        EXPECTED_OUTPUTS = torch.tensor([[147, 2, 54, 2, 43, 2, 169, 122, 29, 64, 2, 136, 37, 33, 9, 8193]])

        self.assertTrue(torch.allclose(autoregressive_model_output, EXPECTED_OUTPUTS))

    def test_text_and_speech_encoder_models(self):
        # check for text embeds
        text_embeds = self.model.text_encoder_model(input_ids=self.text_tokens, return_dict=True)[0].cpu()

        # fmt: off
        EXPECTED_TEXT_EMBEDS = torch.tensor(
            [ 1.4798, -2.0005, 2.3902, -0.5042, 1.6401, -2.4135, -1.4800, 3.0118, -2.4422, 1.3267,
              2.2339, 1.4761, -4.8983, -1.3592, 6.0251, 6.7364, 2.2576, 3.7229, -10.0436, 4.6676,
            ]
        )
        # fmt: on

        self.assertTrue(torch.allclose(text_embeds[0, :20], EXPECTED_TEXT_EMBEDS, atol=1e-4))

        # check for speech embeds
        speech_embeds = self.model.speech_encoder_model(input_ids=self.text_tokens, return_dict=True)[0].cpu()

        # fmt: off
        EXPECTED_SPEECH_EMBEDS = torch.tensor(
            [ 3.1202, -3.1183, -1.4264, -6.1339, 1.8885, -0.1983, 0.9461, -1.7414, 0.3320, -3.8400,
              -1.5715, 1.5096, -1.7576, 0.2387, 4.9758, 5.8450, -6.2534, 2.8586, -5.5816, 4.7821,
            ]
        )
        # fmt: on

        self.assertTrue(torch.allclose(speech_embeds[0, :20], EXPECTED_SPEECH_EMBEDS, atol=1e-4))

    def test_full_model_integration(self):
        full_model_output = self.model.generate(
            input_ids=self.text_tokens,
            input_features=self.input_features,
            do_sample=False,
            num_beams=4,
            num_return_sequences=4,
            max_new_tokens=16,
        ).speech_candidates.cpu()

        EXPECTED_OUTPUTS = torch.tensor([[729, 155, 334], [757, 729, 1305], [729, 757, 334]])

        self.assertTrue(torch.allclose(full_model_output[-3:, -3:], EXPECTED_OUTPUTS))
