# HeadNeRF: A Real-time NeRF-based Parametric Head Model

<span id="code_demo"></span>

This repository contains a pytorch implementation of "HeadNeRF: A Real-time NeRF-based Parametric Head Model (CVPR 2022)".
Authors: [Yang Hong](https://crishy1995.github.io/), Bo Peng, Haiyao Xiao, [Ligang Liu](http://staff.ustc.edu.cn/~lgliu/) and [Juyong Zhang*](http://staff.ustc.edu.cn/~juyong/).
- Requirements
    ```bash
    conda env create -f environment.yaml
    conda activate headnerf
    ```

- [Pytorch3d](https://github.com/facebookresearch/pytorch3d)
    
    It is recommended to install pytorch3d from a local clone. 
    ```bash
    
    git clone https://github.com/facebookresearch/pytorch3d.git
    cd pytorch3d && pip install -e . && cd ..
    ```

## Getting Started
Download [ConfigModels.zip](https://mailustceducn-my.sharepoint.com/:u:/g/personal/hymath_mail_ustc_edu_cn/EVROtiHQqNtLoAlLweyIFV0BDrLcGn_bpHnlM6tREfcQLQ?e=n0howy), [TrainedModels.zip](https://mailustceducn-my.sharepoint.com/:u:/g/personal/hymath_mail_ustc_edu_cn/EZ78EK8mylZBoGe-jCVibjEBVtoicnFvM0jpjaE56F4ihw?e=LC0SH7), and [LatentCodeSamples.zip](https://mailustceducn-my.sharepoint.com/:u:/g/personal/hymath_mail_ustc_edu_cn/EWNBh3ZVo7VFnO9uPRuDYAABbhrx8kyQa53fHv4szdgJ-A?e=bXiAfh), then unzip them to the root dir of this project.  
[lipsync_expert.pth](https://iiitaphyd-my.sharepoint.com/:u:/g/personal/radrabha_m_research_iiit_ac_in/EQRvmiZg-HRAjvI6zqN9eTEBP74KefynCwPWVmF57l-AYA?e=ZRPHKP)


The folder structure is as follows:
```
headnerf
├── ConfigModels
│   ├── faceparsing_model.pth
│   ├── nl3dmm_dict.pkl
│   └── nl3dmm_net_dict.pth
│
├── TrainedModels
│   ├── model_Reso32.pth
│   ├── model_Reso32HR.pth
│   └── model_Reso64.pth
│
└── LatentCodeSamples
    ├── model_Reso32
    │   ├── S001_E01_I01_P02.pth
    │   └── ...
    ├── model_Reso32HR
    │   ├── S001_E01_I01_P02.pth
    │   └── ...
    └── model_Reso64
        ├── S001_E01_I01_P02.pth
        └── ...
```



## Citation
If you find our work useful in your research, please consider citing our paper:
```
@inproceedings{hong2021headnerf,
     author     = {Yang Hong and Bo Peng and Haiyao Xiao and Ligang Liu and Juyong Zhang},
     title      = {HeadNeRF: A Real-time NeRF-based Parametric Head Model},
     booktitle  = {{IEEE/CVF} Conference on Computer Vision and Pattern Recognition (CVPR)},
     year       = {2022}
  }
```
If you have questions, please contact hymath@mail.ustc.edu.cn.

## Acknowledgments
- We use [face-alignment](https://github.com/1adrianb/face-alignment) for detecting 68-facial-landmarks. 
- We use [face-parsing.PyTorch](https://github.com/zllrunning/face-parsing.PyTorch) for generating the head mask. 
- The 3DMM that we use is from [3D face from X](https://arxiv.org/abs/1808.05323) and [Noliner3DMM](https://openaccess.thecvf.com/content_cvpr_2018/papers/Tran_Nonlinear_3D_Face_CVPR_2018_paper.pdf). 
- The code of fitting a single image using 3DMM is modified from [3DMM-Fitting-Pytorch](https://github.com/ascust/3DMM-Fitting-Pytorch).


## License
Academic or non-profit organization noncommercial research use only.
