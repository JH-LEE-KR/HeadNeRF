#!/bin/bash

#SBATCH --job-name=rendering
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH -p batch_agi
#SBATCH -w agi2
#SBATCH --cpus-per-gpu=4
#SBATCH --mem-per-gpu=20G
#SBATCH --time=7-0
#SBATCH -o %N_%x_%j.out
#SBTACH -e %N_%x_%j.err

source /data/jaeho/init.sh
conda activate headnerf

python rendering.py --model_path "TrainedModels/model_Reso32HR.pth" \
                             --img "/data/jaeho/lab/lipsync/LipSync_datasets/HeadNeRF_CNN_datasets/cnn_imgs/" \
                             --mask "/data/jaeho/lab/lipsync/LipSync_datasets/HeadNeRF_CNN_datasets/mask_imgs/" \
                             --para_3dmm "/data/jaeho/lab/lipsync/LipSync_datasets/HeadNeRF_CNN_datasets/cnn_imgs/" \
                             --save_root "./fitting_res" \
                             --target_embedding "LatentCodeSamples/*/S025_E14_I01_P02.pth"