# DeepLearnUtils 
# Copyright (c) 2017-8 Eric Kerfoot, KCL, see LICENSE file

from __future__ import division, print_function
from functools import partial,wraps
import numpy as np
import scipy.ndimage
import scipy.fftpack as ft

import trainutils
        
try:
    from PIL import Image
    pilAvailable=True
except ImportError:
    pilAvailable=False
    

def augment(prob=0.5,applyIndices=None):
    '''
    Creates an augmentation function when applied to a function returning an array modifying callable. The function this
    is applied to is given the list of input arrays as positional arguments and then should return a callable operation
    which performs the augmentation. This wrapper then chooses whether to apply the operation to the arguments and if so
    to which ones. The `prob' argument states the probability the augment is applied, and `applyIndices' gives indices of
    the arrays to apply to (or None for all). The arguments are also keyword arguments in the resulting augment function.
    '''
    def _inner(func):
        @wraps(func)
        def _func(*args,**kwargs):
            _prob=kwargs.pop('prob',prob)
            
            if _prob<1.0 and not trainutils.randChoice(_prob):
                return args
            
            _applyIndices=kwargs.pop('applyIndices',applyIndices)
            
            op=func(*args,**kwargs)
            indices=list(_applyIndices or range(len(args)))
            
            return tuple((op(im) if i in indices else im) for i,im in enumerate(args))
        
        if _func.__doc__:
            _func.__doc__+='''
       
Added keyword arguments:
    prob: probability of applying this augment (default: 0.5)
    applyIndices: indices of arrays to apply augment to (default: None meaning all)
'''        
        return _func
    
    return _inner


def checkSegmentMargin(func):
    '''
    Decorate an augment callable `func` with a check to ensure a given segmentation image in the set does not
    touch the margins of the image when geometric transformations are applied. The keyword arguments `margin`,
    `maxCount` and `nonzeroIndex` are used to check the image at index `nonzeroIndex` has the given margin of
    pixels around its edges, trying `maxCount` number of times to get a modifier by calling `func` before 
    giving up and producing a identity modifier in its place. 
    '''
    @wraps(func)
    def _check(*args,**kwargs):
        margin=max(1,kwargs.pop('margin',5))
        maxCount=max(1,kwargs.pop('maxCount',5))
        nonzeroIndex=kwargs.pop('nonzeroIndex',-1)
        acceptedOutput=False
        
        while maxCount>0 and not acceptedOutput:
            op=func(*args,**kwargs)
            maxCount-=1
            
            if nonzeroIndex==-1:
                acceptedOutput=True
            else:
                seg=op(args[nonzeroIndex]).astype(np.int32)
                acceptedOutput=trainutils.zeroMargins(seg,margin)
                
        if not acceptedOutput:
            op=lambda arr:arr
                
        return op
    
    return _check
            

@augment()
def transpose(*arrs):
    '''Transpose axes 0 and 1 for each of `arrs'.'''
    return partial(np.swapaxes,axis1=0,axis2=1)


@augment()
def flip(*arrs):
    '''Flip each of `arrs' with a random choice of up-down or left-right.'''
    return np.fliplr if trainutils.randChoice() else np.flipud


@augment()
def rot90(*arrs):
    '''Rotate each of `arrs' a random choice of quarter, half, or three-quarter circle rotations.'''
    return partial(np.rot90,k=np.random.randint(1,3))
        

@augment(prob=1.0)
def normalize(*arrs):
    '''Normalize each of `arrs'.'''
    return trainutils.rescaleArray


@augment(prob=1.0)
def randPatch(*arrs,patchSize=(32,32)):
    '''Randomly choose a patch from `arrs' of dimensions `patchSize'.'''
    ph,pw=patchSize
    
    def _randPatch():
        h,w=im.shape[:2]
        ry=np.random.randint(0,h-ph)
        rx=np.random.randint(0,w-pw)
            
        return im[ry:ry+ph,rx:rx+pw]
    
    return _randPatch

        
@augment()
@checkSegmentMargin
def shift(*arrs,dimFract=2,order=3):
    '''Shift arrays randomly by `dimfract' fractions of the array dimensions.'''
    testim=arrs[0]
    x,y=testim.shape[:2]
    shiftx=np.random.randint(-x//dimFract,x//dimFract)
    shifty=np.random.randint(-y//dimFract,y//dimFract)
    
    def _shift(im):
        h,w=im.shape[:2]
        dest=np.zeros_like(im)

        srcslices,destslices=trainutils.copypasteArrays(im,dest,(h//2+shiftx,w//2+shifty),(h//2,w//2),(h,w))
        dest[destslices]=im[srcslices]
        
        return dest
            
    return _shift


@augment()
@checkSegmentMargin
def rotate(*arrs):
    '''Shift arrays randomly around the array center.'''
    
    angle=np.random.random()*360
    
    def _rotate(im):
        return scipy.ndimage.rotate(im,angle=angle,reshape=False)
    
    return _rotate


@augment()
@checkSegmentMargin
def zoom(*arrs,zoomrange=0.2):
    '''Return the image/mask pair zoomed by a random amount with the mask kept within `margin' pixels of the edges.'''
    
    z=zoomrange-np.random.random()*zoomrange*2
    zx=z+1.0+zoomrange*0.25-np.random.random()*zoomrange*0.5
    zy=z+1.0+zoomrange*0.25-np.random.random()*zoomrange*0.5
        
    def _zoom(im):
        ztemp=scipy.ndimage.zoom(im,(zx,zy)+tuple(1 for _ in range(2,im.ndim)),order=2)
        return trainutils.resizeCenter(ztemp,*im.shape)
            
    return _zoom


@augment()
@checkSegmentMargin
def rotateZoomPIL(*arrs,margin=5,minFract=0.5,maxFract=2,resample=0):
    assert all(a.ndim>=2 for a in arrs)
    assert pilAvailable,'PIL (pillow) not installed'
    
    testim=arrs[0]
    x,y=testim.shape[:2]
    
    angle=np.random.random()*360
    zoomx=x+np.random.randint(-x*minFract,x*maxFract)
    zoomy=y+np.random.randint(-y*minFract,y*maxFract)
    
    filters=(Image.NEAREST,Image.LINEAR,Image.BICUBIC)
    
    def _trans(im):
        if im.dtype!=np.float32:
            return _trans(im.astype(np.float32)).astype(im.dtype)
        elif im.ndim==2:
            im=Image.fromarray(im)
            
            # rotation
            im=im.rotate(angle,filters[resample])

            # zoom
            zoomsize=(zoomx,zoomy)
            pastesize=(im.size[0]//2-zoomsize[0]//2,im.size[1]//2-zoomsize[1]//2)
            newim=Image.new('F',im.size)
            newim.paste(im.resize(zoomsize,filters[resample]),pastesize)
            im=newim
            
            return np.array(im)
        else:
            return np.dstack([_trans(im[...,i]) for i in range(im.shape[-1])])
            
    return _trans

  
@augment()
def deformPIL(*arrs,defrange=25,numControls=3,margin=2,mapOrder=1):
    '''Deforms arrays randomly with a deformation grid of size `numControls'**2 with `margins' grid values fixed.'''
    assert pilAvailable,'PIL (pillow) not installed'
    
    h,w = arrs[0].shape[:2]
    
    imshift=np.zeros((2,numControls+margin*2,numControls+margin*2))
    imshift[:,margin:-margin,margin:-margin]=np.random.randint(-defrange,defrange,(2,numControls,numControls))

    imshiftx=np.array(Image.fromarray(imshift[0]).resize((w,h),Image.QUAD))
    imshifty=np.array(Image.fromarray(imshift[1]).resize((w,h),Image.QUAD))
        
    y,x=np.meshgrid(np.arange(w), np.arange(h))
    indices=np.reshape(x+imshiftx, (-1, 1)),np.reshape(y+imshifty, (-1, 1))

    def _mapChannels(im):
        if im.ndim==2:
            result=scipy.ndimage.map_coordinates(im,indices, order=mapOrder, mode='constant')
            result=result.reshape(im.shape)
        else:
            result=np.dstack([_mapChannels(im[...,i]) for i in range(im.shape[-1])])
            
        return result
    
    return _mapChannels


@augment()
def distortFFT(*arrs,minDist=0.1,maxDist=1.0):
    '''Distorts arrays by applying dropout in k-space with a per-pixel probability based on distance from center.'''
    h,w=arrs[0].shape[:2]

    x,y=np.meshgrid(np.linspace(-1,1,h),np.linspace(-1,1,w))
    probfield=np.sqrt(x**2+y**2)
    
    if arrs[0].ndim==3:
        probfield=np.repeat(probfield[...,np.newaxis],arrs[0].shape[2],2)
    
    dropout=np.random.uniform(minDist,maxDist,arrs[0].shape)>probfield

    def _distort(im):
        if im.ndim==2:
            result=ft.fft2(im)
            result=ft.fftshift(result)
            result=result*dropout[:,:,0]
            result=ft.ifft2(result)
            result=np.abs(result)
        else:
            result=np.dstack([_distort(im[...,i]) for i in range(im.shape[-1])])
            
        return result
    
    return _distort


def splitSegmentation(*arrs,numLabels=2,segIndex=-1):
    arrs=list(arrs)
    seg=arrs[segIndex]
    seg=trainutils.oneHot(seg,numLabels)
    arrs[segIndex]=seg
    
    return tuple(arrs)


def mergeSegmentation(*arrs,segIndex=-1):
    arrs=list(arrs)
    seg=arrs[segIndex]
    seg=np.argmax(seg,2)
    arrs[segIndex]=seg
    
    return tuple(arrs)


if __name__=='__main__':
    
    im=np.random.rand(128,128,1)
    
    imt=transpose(im,prob=1.0)
    print(np.all(im.T==imt[0]))
    
    imf=flip(im,prob=1.0)
    imr=rot90(im,prob=1.0)
    
    print(randPatch(im,patchSize=(30,34))[0].shape)
    
    print(shift(im,prob=1.0)[0].shape)
    
    print(rotate(im,prob=1.0)[0].shape)
    
    print(zoom(im,prob=1.0)[0].shape)
    
    print(deformPIL(im,prob=1.0)[0].shape)
    
    print(distortFFT(im,prob=1.0)[0].shape)
    