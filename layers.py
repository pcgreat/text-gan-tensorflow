""" TensorFlow Layers

Convenience functions but Input and Output should be tensors.
"""

import tensorflow as tf
import myseq2seq as seq2seq


_phase = tf.Variable(False, name='phase', trainable=False, collections=[tf.GraphKeys.LOCAL_VARIABLES])
_phase_train = _phase.assign(True)
_phase_infer = _phase.assign(False)


# TODO: move to ops
def _rank(x):
    return len(x.get_shape())


def _apply_dropout_mask(tensor_shape, keep_prob=1.0, normalize=True):
    random_tensor = keep_prob + tf.random_uniform(tensor_shape, dtype=tf.float32)
    binary_mask = tf.floor(random_tensor)
    if normalize:
        binary_mask = tf.reciprocal(keep_prob) * binary_mask
    return binary_mask


def _global_keep_prob(keep_prob):
    keep_prob = tf.convert_to_tensor(keep_prob, dtype=tf.float32)
    keep_prob = tf.cond(_phase, lambda: keep_prob, lambda: keep_prob * 0.0 + 1.0)
    return keep_prob


def layer(func):

    class Layer(object):
        def __init__(self, *args, **kwargs):
            self.func = func
            self.args = args
            self.kwargs = kwargs
            self.name = self.kwargs.get("name", self.func.__name__)

            self._template = tf.make_template(self.name, self.func, create_scope_now_=True)
            self._unique_name = self._template.variable_scope.name.split("/")[-1]
            self._summary_added = False

        def __call__(self, x):
            out = self.template(x, *self.args, **self.kwargs)
            self._layer_logging(x, out)
            self._add_summary()
            return out

        def __rrshift__(self, other):
            """ >> """
            return self.__call__(other)

        def _layer_logging(self, other, out):
            tf.logging.info("     {} {} {} -> {}".format(
                self.unique_name, "shape", str(other.get_shape()), str(out.get_shape())))

        def _add_summary(self):
            if not self.kwargs.get("summary"):
                return None
            if self.summary_added:
                return None
            for var in self.get_variables_in_scope():
                # TODO: different summary types
                tf.summary.scalar(var.name, tf.reduce_mean(var))
            self._summary_added = True

        def get_variables_in_scope(self):
            assert self.template._variables_created, "Variables not yet created or undefined."
            variables = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.variable_scope_name)
            return variables

        @property
        def template(self):
            return self._template

        @property
        def unique_name(self):
            return self._unique_name

        @property
        def variable_scope_name(self):
            return self.template._variable_scope._name

        @property
        def summary_added(self):
            return self._summary_added

    return Layer


@layer
def identity_layer(tensor, **opts):
    out = tf.identity(tensor)
    return out


@layer
def embedding_layer(tensor, vocab_size=None, embedding_dim=None, embedding_matrix=None, **opts):
    if embedding_matrix is None:
        initializer = tf.contrib.layers.xavier_initializer(uniform=True)
        embedding_matrix = tf.get_variable("embedding_matrix", initializer=initializer(shape=(vocab_size, embedding_dim)))

    out = tf.nn.embedding_lookup(embedding_matrix, tensor)
    return out


@layer
def recurrent_layer(tensor, cell=None, hidden_dims=128, sequence_length=None, decoder_fn=None, 
                    activation=tf.nn.tanh, initializer=tf.orthogonal_initializer(), initial_state=None, 
                    keep_prob=1.0,
                    return_final_state=False, return_next_cell_input=True, **opts):
    if cell is None:
        cell = tf.contrib.rnn.BasicRNNCell(hidden_dims, activation=activation)
        # cell = tf.contrib.rnn.LSTMCell(hidden_dims, activation=activation)

    if keep_prob < 1.0:
        keep_prob = _global_keep_prob(keep_prob)
        cell = tf.contrib.rnn.DropoutWrapper(cell, keep_prob, keep_prob)

    if opts.get("name"):
        tf.add_to_collection(opts.get("name"), cell)

    if decoder_fn is None:
        outputs, final_state = tf.nn.dynamic_rnn(cell, tensor, 
            sequence_length=sequence_length, initial_state=initial_state, dtype=tf.float32)
        final_context_state = None
    else:
        # TODO: turn off sequence_length?
        outputs, final_state, final_context_state = seq2seq.dynamic_rnn_decoder(
            cell, decoder_fn, inputs=None, sequence_length=sequence_length)






    if return_final_state:
        return final_state
    else:
        return outputs


@layer
def reshape_layer(tensor, shape, **opts):
    out = tf.reshape(tensor, shape=shape)
    return out


@layer
def dense_layer(tensor, hidden_dims, weight=None, bias=None, **opts):
    original_tensor_shape = tf.shape(tensor)
    in_dim = int(tensor.get_shape()[-1])

    rank = _rank(tensor)
    if rank > 2:
        # -- time distributed dense
        tensor = tf.reshape(tensor, shape=(-1, in_dim))

    name = opts.get("name", "")

    if weight is None:
        initializer = tf.contrib.layers.xavier_initializer(uniform=True)
        weight = tf.get_variable("{}_dense_W".format(name), initializer=initializer(shape=(in_dim, hidden_dims)))
    if bias is None:
        bias = tf.get_variable("{}_dense_b".format(name), initializer=tf.zeros(shape=hidden_dims))

    out = tf.add(tf.matmul(tensor, weight), bias)

    if rank > 2:
        # reshape back to time dimension
        out = tf.reshape(out, shape=original_tensor_shape)

    return out


@layer
def dropout_layer(tensor, keep_prob=1.0, **opts):
    keep_prob = _global_keep_prob(keep_prob)
    out = tf.nn.dropout(tensor, keep_prob=keep_prob)
    return out


# TODO: should i normalize?
@layer
def word_dropout_layer(tensor, keep_prob=1.0, **opts):
    keep_prob = _global_keep_prob(keep_prob)

    rank = _rank(tensor)
    assert rank == 3, "Use embedding lookup layer"

    binary_mask = _apply_dropout_mask(tf.shape(tensor)[:2], keep_prob, normalize=False)
    binary_mask = tf.expand_dims(binary_mask, axis=-1)  # proper broadcasting to zero out entire word vectors

    out = tensor * binary_mask
    return out


@layer
def relu_layer(tensor):
    out = tf.nn.relu(tensor)
    return out


@layer
def tanh_layer(tensor):
    out = tf.nn.tanh(tensor)
    return out


@layer
def softmax_layer(tensor, softmax_func=None, **opts):
    if softmax_func is None:
        softmax_func = tf.nn.softmax

    out = softmax_func(tensor)
    return out


@layer
def cross_entropy_layer(tensor, target, **opts):
    if _rank(tensor) > 1:
        target = tf.reshape(target, shape=(-1, ))

    cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=tensor, labels=target)
    mask = tf.cast(tf.not_equal(target, tf.zeros_like(target)), dtype=tf.float32)
    out = cross_entropy * mask
    return out


@layer
def sigmoid_cross_entropy_layer(tensor, target, **opts):
    out = tf.nn.sigmoid_cross_entropy_with_logits(logits=tensor, labels=target)
    return out


@layer
def mean_loss_by_example_layer(tensor, sequence_length, **opts):
    loss = tf.div(
        tf.reduce_sum(tensor, axis=1),
        tf.cast(sequence_length, dtype=tf.float32)
    )
    out = tf.reduce_mean(loss)
    tf.summary.scalar('cost', out)
    return out


@layer
def conv1d_layer(tensor, dilation_rate=1, **opts):
    raise NotImplementedError


@layer
def residual_layer(tensor, **opts):
    raise NotImplementedError


@layer
def highway_layer(tensor, **opts):
    raise NotImplementedError


if __name__ == "__main__":
    import numpy as np

    batch_size = 10
    sequence_length = 5
    vocab_size = 100
    embedding_dim = 32

    word_ids = np.random.randint(0, vocab_size, batch_size * sequence_length).reshape(batch_size, sequence_length)
    tensor = tf.constant(word_ids)

    # print(word_ids >> identity_layer() >> embedding_layer(vocab_size, embedding_dim))
    print(tensor >> identity_layer() >> embedding_layer(vocab_size, embedding_dim))
