import tensorflow as tf
from tensorflow.keras.layers import (Add, Conv2D, Input, Concatenate, Dropout, 
                                     GaussianDropout, UpSampling2D, 
                                     SpatialDropout2D)
from tensorflow.keras.models import Model

from .blocks import (ResidualBlock, ConvBlock, DenseBlock, TransitionBlock,
                     LocalizedConvBlock, SubpixelConvolutionBlock, 
                     DeconvolutionBlock, EncoderBlock, PadConcat, MCDropout, 
                     MCSpatialDropout2D, MCGaussianDropout)
from ..utils import checkarg_backbone, checkarg_dropout_variant
 

def net_pin(
    backbone_block,
    n_channels, 
    n_aux_channels,
    n_filters, 
    n_blocks, 
    hr_size,
    n_channels_out=1, 
    activation='relu',
    dropout_rate=0.2,
    dropout_variant='spatial',
    normalization=None,
    attention=False,
    output_activation=None,
    localcon_layer=False):
    """
    Deep neural network with different backbone architectures (according to the
    ``backbone_block``) and pre-upsampling via interpolation.

    The interpolation method depends on the ``interpolation`` argument used in
    the training procedure (which is passed to the DataGenerator).
    """
    backbone_block = checkarg_backbone(backbone_block)
    dropout_variant = checkarg_dropout_variant(dropout_variant)

    h_hr = hr_size[0]
    w_hr = hr_size[1]

    auxvar_array_is_given = True if n_aux_channels > 0 else False
    if auxvar_array_is_given:
        if not localcon_layer:
            s_in = Input(shape=(None, None, n_aux_channels))
        else:
            s_in = Input(shape=(h_hr, w_hr, n_aux_channels))

    if not localcon_layer:  
        x_in = Input(shape=(None, None, n_channels))
    else:
        x_in = Input(shape=(h_hr, w_hr, n_channels))
    x = b = Conv2D(n_filters, (3, 3), padding='same')(x_in)

    #---------------------------------------------------------------------------
    # N conv blocks
    for i in range(n_blocks):
        if backbone_block == 'convnet':
            b = ConvBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
        elif backbone_block == 'resnet':
            b = ResidualBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
        elif backbone_block == 'densenet':
            b = DenseBlock(
                n_filters, activation=activation, dropout_rate=dropout_rate, 
                dropout_variant=dropout_variant, normalization=normalization, 
                attention=attention)(b)
            b = TransitionBlock(n_filters // 2)(b)  # another option: half of the DenseBlock channels
    b = Conv2D(n_filters, (3, 3), padding='same')(b)
    
    if dropout_rate > 0:
        if dropout_variant is None:
            b = Dropout(dropout_rate)(b)
        elif dropout_variant == 'gaussian':
            b = GaussianDropout(dropout_rate)(b)
        elif dropout_variant == 'spatial':
            b = SpatialDropout2D(dropout_rate)(b)
        elif dropout_variant == 'mcdrop':
            b = MCDropout(dropout_rate)(b)
        elif dropout_variant == 'mcgaussiandrop':
            b = MCGaussianDropout(dropout_rate)
        elif dropout_variant == 'mcspatialdrop':
            b = MCSpatialDropout2D(dropout_rate)

    if backbone_block == 'convnet':
        x = b
    elif backbone_block == 'resnet':
        x = Add()([x, b])
    elif backbone_block == 'densenet':
        x = Concatenate()([x, b])
    
    #---------------------------------------------------------------------------
    # Localized convolutional layer
    if localcon_layer:
        lws = LocalizedConvBlock(filters=2, use_bias=True)(x)
        x = Concatenate()([x, lws])

    #---------------------------------------------------------------------------
    # HR aux channels are processed
    if auxvar_array_is_given:
        s = ConvBlock(
            n_filters, activation=activation, dropout_rate=0, 
            normalization=normalization, attention=False)(s_in)   
        x = Concatenate()([x, s])   

    #---------------------------------------------------------------------------
    # Last conv layers
    x = ConvBlock(n_filters, activation=None, dropout_rate=dropout_rate, 
        normalization=normalization, attention=True)(x)  

    x = ConvBlock(n_channels_out, activation=output_activation, dropout_rate=0, 
        normalization=normalization, attention=False)(x)     
    
    model_name = backbone_block + '_pin'
    if auxvar_array_is_given:
        return Model(inputs=[x_in, s_in], outputs=x, name=model_name)  
    else:
        return Model(inputs=[x_in], outputs=x, name=model_name)


def unet_pin(
    backbone_block,
    n_channels, 
    n_aux_channels,
    n_filters, 
    n_blocks, 
    hr_size,
    n_channels_out=1, 
    activation='relu',
    dropout_rate=0.2,
    dropout_variant='spatial',
    normalization=None,
    attention=False,
    decoder_upsampling='rc',
    output_activation=None,
    localcon_layer=False):
    """    
    Deep neural network with UNET (encoder-decoder) backbone and pre-upsampling 
    via interpolation.

    The interpolation method depends on the ``interpolation`` argument used in
    the training procedure (which is passed to the DataGenerator).
    """
    backbone_block = checkarg_backbone(backbone_block)
    dropout_variant = checkarg_dropout_variant(dropout_variant)
    n_blocks = _check_nblocks(hr_size, n_blocks)
    h_hr = hr_size[0]
    w_hr = hr_size[1]

    auxvar_array_is_given = True if n_aux_channels > 0 else False
    if auxvar_array_is_given:
        if not localcon_layer and h_hr == w_hr:
            s_in = Input(shape=(None, None, n_aux_channels))
        else:
            s_in = Input(shape=(h_hr, w_hr, n_aux_channels))

    if not localcon_layer and h_hr == w_hr:  
        x_in = Input(shape=(None, None, n_channels))
    else:
        x_in = Input(shape=(h_hr, w_hr, n_channels))

    #---------------------------------------------------------------------------
    # n enconding conv blocks
    x = x_in
    enconding_filters = []
    n_filters_list = []
    for i in range(n_blocks):
        x, x_skipcon = EncoderBlock(n_filters, activation, dropout_rate,
            dropout_variant, normalization, attention, name_suffix=str(i+1))(x)
        enconding_filters.append(x_skipcon)
        n_filters_list.append(n_filters)
        n_filters = min(256, n_filters * 2)   # doubling # of filters with each encoding layer, capping at 256

    # bottleneck layer
    x = ConvBlock(n_filters, activation=activation, dropout_rate=dropout_rate, 
        dropout_variant=dropout_variant, name='Bottleneck',
        normalization=None)(x)     # following Isola et al 2016

    # n decoding conv blocks
    n_filters_list = n_filters_list[::-1]
    for j, skip_connection in enumerate(reversed(enconding_filters)):        
        n_filters = n_filters_list[j]
        if decoder_upsampling == 'spc':
            x = SubpixelConvolutionBlock(2, n_filters, name_suffix=str(j+1))(x)
        elif decoder_upsampling == 'rc':
            x = UpSampling2D(2, interpolation='bilinear', name='BilinearUpsampling'+str(j+1))(x)
        elif decoder_upsampling == 'dc':
            x = DeconvolutionBlock(2, n_filters, activation, name_suffix=str(j+1))(x)

        x = PadConcat(name_suffix='_SkipConnection'+str(j+1))([x, skip_connection])        
        x = ConvBlock(n_filters, activation=activation, dropout_rate=dropout_rate, 
            dropout_variant=dropout_variant, normalization=normalization, 
            attention=attention, name='DecoderConvBlock' + str(j+1))(x)

    #---------------------------------------------------------------------------
    # Localized convolutional layer
    if localcon_layer:
        lws = LocalizedConvBlock(filters=2, use_bias=True)(x)
        x = Concatenate()([x, lws])

    #---------------------------------------------------------------------------
    # HR aux channels are processed
    if auxvar_array_is_given:
        s = ConvBlock(n_filters, activation=activation, dropout_rate=0, 
            normalization=normalization, attention=False)(s_in)   
        x = Concatenate()([x, s])   

    #---------------------------------------------------------------------------
    # Last conv layers
    x = ConvBlock(n_filters, activation=None, dropout_rate=dropout_rate, 
        normalization=normalization, attention=True)(x)  

    x = ConvBlock(n_channels_out, activation=output_activation, dropout_rate=0, 
        normalization=normalization, attention=False)(x)     
    
    model_name = backbone_block + '_pin'
    if auxvar_array_is_given:
        return Model(inputs=[x_in, s_in], outputs=x, name=model_name)  
    else:
        return Model(inputs=[x_in], outputs=x, name=model_name)


def _check_nblocks(shape, power):  
    while shape[0] // 2**power < 2 or shape[1] // 2**power < 2:
        msg = f'`n_blocks` is too large, cannot downsample {power} times '
        msg += f'given the input grid size. Setting `n_blocks` to {power-1}'
        print(msg)
        power -= 1
    return power

