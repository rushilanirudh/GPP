# Copyright 2020 Lawrence Livermore National Security, LLC and other authors: Rushil Anirudh, Suhas Lohit, Pavan Turaga
# SPDX-License-Identifier: MIT
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torchvision.transforms as transforms
from torch.utils.data import Dataset, DataLoader, TensorDataset
import torchvision.utils as vutils
import torch.nn.functional as nnf
import random

import matplotlib.pyplot as plt
import numpy as np
import os

from skimage.transform import rescale, resize
from skimage import io
from skimage.measure import compare_psnr
from skimage.color import rgb2gray

from PIL import Image

from models import *
from utils import *


USE_BM3D = False
savedir = './outs2'
genPATH = './all_models/grayscale_generator.model'
filename = 'boats'
fname = '../test_images/{}.tif'.format(filename)

if not os.path.exists(savedir):
    os.makedirs(savedir)

I_y = 256
I_x = 256
d_x = d_y = 32
dim_x = d_x*d_y
batch_size = (I_x*I_y)//(dim_x)
n_measure = 0.1
nz = 100

dim_phi = int(n_measure*dim_x)
nIter = 10001
n_img_plot_x = I_x//d_x
n_img_plot_y = I_y//d_y
workers = 2
ngpu = 1
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
## measurement operator
phi_np = np.random.randn(dim_x,dim_phi)
phi_test = torch.Tensor(phi_np)


if USE_BM3D:
    from bm3d import bm3d, BM3DProfile
    from experiment_funcs import get_experiment_noise
    noise_type = 'g0'
    noise_var = 0.02 # Noise variance
    seed = 0  # seed for pseudorandom noise realization

    # Generate noise with given PSD
    noise, psd, kernel = get_experiment_noise(noise_type, noise_var, seed, [256,256])
    ###

def cs_measure(gt,est,phi):
    n_dim = gt.shape[2]* gt.shape[3]
    y_gt = torch.matmul(gt.view(-1,n_dim),phi)
    y_est = torch.matmul(est.view(-1,n_dim),phi)
    return y_gt,y_est


x_test = Image.open(fname).convert(mode='L').resize((I_x,I_y))
x_test_ = np.expand_dims(np.array(x_test)/255.,axis=2)
torch_gt = torch.Tensor(np.transpose(x_test_,[2,0,1]))
print(x_test_.shape, np.max(x_test_), np.min(x_test_))
io.imsave('{}/gt.png'.format(savedir),(255*x_test_[:,:,0]).astype(np.uint8))
# x_test_ = 2*x_test_-1
x_test = []
for i in range(n_img_plot_x):
    for j in range(n_img_plot_y):
        _x = x_test_[i*d_x:d_x*(i+1),j*d_y:d_y*(j+1)]
        x_test.append(_x)

x_test = np.array(x_test)
print(x_test.shape)

test_images = torch.Tensor(np.transpose(x_test[:batch_size,:,:,:],[0,3,1,2]))

netG = Generator(ngpu=ngpu,nc=1).to(device)

if (device.type == 'cuda') and (ngpu > 1):
    netG = nn.DataParallel(netG, list(range(ngpu)))

netG.apply(weights_init)


if os.path.isfile(genPATH):
    print('**** Loading Generator ****')
    if device.type == 'cuda':
        netG.load_state_dict(torch.load(genPATH))
    elif device.type=='cpu':
        netG.load_state_dict(torch.load(genPATH,map_location=torch.device('cpu')))
    else:
        raise Exception("Unable to load model to specified device")

    netG.eval()

for param in netG.parameters():
        param.requires_grad = False

criterion = nn.MSELoss()
z_prior = torch.zeros(batch_size,nz,1,1,requires_grad=True,device=device)

optimizerZ = optim.RMSprop([z_prior], lr=8e-4)
lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer=optimizerZ, gamma=0.99)
real_cpu = test_images.to(device)

for iters in range(nIter):
    # z2 = torch.clamp(z_prior,-1.,1.)
    fake = 0.5*netG(z_prior)+0.5
    fake = nnf.interpolate(fake, size=(d_x, d_y), mode='bilinear', align_corners=False)
    y_gt,y_est = cs_measure(real_cpu,fake,phi_test.to(device))
    cost = criterion(y_gt,y_est)
    optimizerZ.zero_grad()
    cost.backward()
    optimizerZ.step()
    if (iters+1)%100==0:
        lr_scheduler.step()

    if (iters % 500 == 0):

        with torch.no_grad():
            fake = 0.5*netG(z_prior).detach().cpu()+0.5
            fake = nnf.interpolate(fake, size=(d_x, d_y), mode='bilinear', align_corners=False)
        G_imgs = np.transpose(fake.detach().cpu().numpy(),[0,2,3,1])
        imgest = merge(G_imgs,[n_img_plot_x,n_img_plot_y])



        psnr0 = compare_psnr(x_test_[:,:,0],imgest,data_range=1.0)
        if USE_BM3D:
            merged_clean = bm3d(imgest,psd)
            psnr1 = compare_psnr(x_test_[:,:,0],merged_clean,data_range=1.0)
            display = merged_clean
        else:
            print('PSNR and reconstructions are without BM3D')
            display = imgest
            psnr1 = psnr0


        print('Iter: {:d}, Error: {:.3f}, PSNR: {:.3f}, PSNR-bm3d: {:.3f}, Current LR:{:.5f} '.format(iters,cost.item(),psnr0,psnr1,lr_scheduler.get_last_lr()[0]))
        plt.imshow(display,cmap='gray')
        plt.axis('off')
        plt.text(50,240,"PSNR: {:.2f} dB".format(psnr1), color="blue", fontdict={"fontsize":20, "ha":"left", "va":"baseline"},bbox=dict(facecolor='white', alpha=0.6))
        plt.savefig('outs2/inv_solution_{}.png'.format(str(iters).zfill(5)),bbox_inches = 'tight',pad_inches = 0)
        plt.close()
