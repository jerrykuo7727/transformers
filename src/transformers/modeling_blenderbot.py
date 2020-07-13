from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from transformers.modeling_bart import SelfAttention

from .configuration_blenderbot import BlenderbotConfig
from .file_utils import add_start_docstrings_to_callable
from .modeling_bart import BartDecoder, BartEncoder, PretrainedBartModel, _prepare_bart_decoder_inputs, _reorder_buffer
from .modeling_outputs import (
    BaseModelOutput,
    BaseModelOutputWithPast,
    Seq2SeqLMOutput,
    Seq2SeqModelOutput,
    Seq2SeqQuestionAnsweringModelOutput,
    Seq2SeqSequenceClassifierOutput,
)


# TODO: delete this
BLENDERBOT_PRETRAINED_MODEL_ARCHIVE_LIST = ["sshleifer/blenderbot-3B", "sshleifer/blenderbot-90M"]


# class BlenderbotDecoder(BartDecoder):
#
#     """
#     This class inherits BartDecoder. Please check the superclass for documentation and usage examples.
#     """
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         for layer in self.layers:
#             # func_type = type(layer.encoder_attn._shape)
#             layer.encoder_attn.mode = "parlai"
#             # layer.encoder_attn = BlenderbotCrossAttention(bart_attn.embed_dim, bart_attn.num_heads,
#             #                                                      dropout=bart_attn.dropout, bias=True,
#             #                                                      encoder_decoder_attention=True)
#             # layer.encoder_attn._shape = func_type(blenderbot_shape, layer, SelfAttention)




BLENDERBOT_START_DOCSTRING = r"""
    This model is a PyTorch `torch.nn.Module <https://pytorch.org/docs/stable/nn.html#torch.nn.Module>`_ sub-class.
    Use it as a regular PyTorch Module and refer to the PyTorch documentation for all matter related to general
    usage and behavior.
    Args:
        config (:class:`~transformers.BlenderbotConfig`): Model configuration class with all the parameters of the model.
            Initializing with a config file does not load the weights associated with the model, only the configuration.
            Check out the :meth:`~transformers.PreTrainedModel.from_pretrained` method to load the model weights.
"""
BLENDERBOT_INPUTS_DOCSTRING = r"""
 
 Args:
        input_ids (:obj:`torch.LongTensor` of shape :obj:`(batch_size, input_ids_length)`):
            Indices of input sequence tokens in the vocabulary.
            Indices can be obtained using :class:`transformers.BlenderbotTokenizer`.
            See :func:`transformers.PreTrainedTokenizer.encode` and
            :func:`transformers.PreTrainedTokenizer.encode_plus` for details.
            `What are input IDs? <../glossary.html#input-ids>`__
        encoder_outputs (:obj:`tuple(tuple(torch.FloatTensor)`, `optional`, defaults to :obj:`None`):
            Tuple consists of (`last_hidden_state`, `optional`: `hidden_states`, `optional`: `attentions`)
            `last_hidden_state` of shape :obj:`(batch_size, sequence_length, hidden_size)`, `optional`, defaults to :obj:`None`) 
       attention_mask (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Mask to avoid performing attention on padding token indices.
            Mask values selected in ``[0, 1]``:
            ``1`` for tokens that are NOT MASKED, ``0`` for MASKED tokens.
            `What are attention masks? <../glossary.html#attention-mask>`__
        decoder_input_ids (:obj:`torch.LongTensor` of shape :obj:`(batch_size, target_sequence_length)`, `optional`, defaults to :obj:`None`):
        decoder_attention_mask (:obj:`torch.BoolTensor` of shape :obj:`(batch_size, tgt_seq_len)`, `optional`, defaults to :obj:`None`):
        labels: (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
"""


class BlenderbotForConditionalGeneration(PretrainedBartModel):
    config_class = BlenderbotConfig
    base_model_prefix = "."

    def __init__(self, config: BlenderbotConfig):
        super().__init__(config)
        # self.config = config
        self.shared = nn.Embedding(config.vocab_size, config.d_model, config.pad_token_id)
        self.encoder = BartEncoder(config, self.shared)
        self.decoder = BartDecoder(config, self.shared)
        self.init_weights()

    @add_start_docstrings_to_callable(BLENDERBOT_INPUTS_DOCSTRING)
    def forward(
        self,
        input_ids,
        encoder_outputs=None,
        decoder_input_ids=None,
        attention_mask=None,
        decoder_attention_mask=None,
        labels=None,
        decoder_cached_states=None,
        use_cache=False,
        output_attentions=None,
        output_hidden_states=None,
        return_tuple=None,
    ):
        if decoder_input_ids is None:
            use_cache = False

        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        use_cache = use_cache if use_cache is not None else self.config.use_cache
        return_tuple = return_tuple if return_tuple is not None else self.config.use_return_tuple

        if encoder_outputs is None:
            encoder_outputs = self.encoder(
                input_ids=input_ids,
                attention_mask=attention_mask,
                output_attentions=output_attentions,
                output_hidden_states=output_hidden_states,
                return_tuple=return_tuple,
            )
        # If the user passed a tuple for encoder_outputs, we wrap it in a BaseModelOuput when return_tuple=False
        elif not return_tuple and not isinstance(encoder_outputs, BaseModelOutput):
            encoder_outputs = BaseModelOutput(
                last_hidden_state=encoder_outputs[0],
                hidden_states=encoder_outputs[1] if len(encoder_outputs) > 1 else None,
                attentions=encoder_outputs[2] if len(encoder_outputs) > 2 else None,
            )
        assert isinstance(encoder_outputs, BaseModelOutput)
        if use_cache:
            decoder_padding_mask, casual_mask = None, None
        else:
            decoder_input_ids, decoder_padding_mask, casual_mask = _prepare_bart_decoder_inputs(
                self.config,
                input_ids,
                decoder_input_ids=decoder_input_ids,
                causal_mask_dtype=self.shared.weight.dtype,
                decoder_padding_mask=decoder_attention_mask,
            )
        assert decoder_input_ids is not None
        decoder_outputs = self.decoder(
            decoder_input_ids,
            encoder_outputs[0],
            attention_mask,
            decoder_padding_mask,
            decoder_causal_mask=casual_mask,
            decoder_cashed_states=decoder_cached_states,
            use_cache=use_cache,
        )

        scores = F.linear(decoder_outputs[0], self.shared.weight)
        # outputs = (scores,) + outputs[1:]
        loss = None
        if labels is not None:
            loss_fc = nn.CrossEntropyLoss()
            loss = loss_fc(scores[0].view(-1, self.config.vocab_size), labels.view(-1))

        return Seq2SeqLMOutput(
            loss=loss,
            logits=scores,
            decoder_past_key_values=decoder_outputs.past_key_values,
            decoder_hidden_states=decoder_outputs.hidden_states,
            decoder_attentions=decoder_outputs.attentions,
            encoder_last_hidden_state=encoder_outputs.last_hidden_state,
            encoder_hidden_states=encoder_outputs.hidden_states,
            encoder_attentions=encoder_outputs.attentions,
        )

    def prepare_logits_for_generation(self, logits, cur_len, max_length):
        # force the start token  probability of generation to be 0.
        logits[:, self.config.bos_token_id] = float("-inf")
        return logits

    def prepare_inputs_for_generation(self, decoder_input_ids, past, attention_mask, use_cache, **kwargs):
        # exactly as in BartConditionalGeneration
        assert past is not None, "past has to be defined for encoder_outputs"
        # first step, decoder_cached_states are empty
        encoder_outputs, decoder_cached_states = past
        return {
            "input_ids": None,
            "encoder_outputs": encoder_outputs,
            "decoder_cached_states": decoder_cached_states,
            "decoder_input_ids": decoder_input_ids,
            "attention_mask": attention_mask,
            "use_cache": use_cache,
        }

    def get_input_embeddings(self):
        return self.shared

    def set_input_embeddings(self, value):
        self.shared = value
        self.encoder.embed_tokens = self.shared
        self.decoder.embed_tokens = self.shared

    def get_output_embeddings(self):
        vocab_size, embed_dim = self.shared.weight.shape
        lin_layer = nn.Linear(vocab_size, embed_dim, bias=False)
        lin_layer.weight.data = self.shared.weight.data
        return lin_layer

    def get_encoder(self):
        return self.encoder

    @staticmethod
    def _reorder_cache(past, beam_idx):
        # exactly as in BartConditionalGenerator
        ((enc_out, enc_mask), decoder_cached_states) = past
        reordered_past = []
        for layer_past in decoder_cached_states:
            # get the correct batch idx from decoder layer's batch dim for cross and self-attn
            layer_past_new = {
                attn_key: _reorder_buffer(attn_cache, beam_idx) for attn_key, attn_cache in layer_past.items()
            }
            reordered_past.append(layer_past_new)

        new_enc_out = enc_out if enc_out is None else enc_out.index_select(0, beam_idx)
        new_enc_mask = enc_mask if enc_mask is None else enc_mask.index_select(0, beam_idx)

        past = ((new_enc_out, new_enc_mask), reordered_past)
        return past