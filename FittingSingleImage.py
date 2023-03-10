from os.path import join
import os
import torch
import numpy as np
from NetWorks.HeadNeRFNet import HeadNeRFNet
import cv2
from HeadNeRFOptions import BaseOptions
from Utils.HeadNeRFLossUtils import HeadNeRFLossUtils
from Utils.RenderUtils import RenderUtils
import pickle as pkl
import time
from glob import glob
from tqdm import tqdm
import imageio
import random
import argparse
from tool_funcs import put_text_alignmentcenter

from NetWorks.models import AudioNet, AudioAttNet
import audio
from hparams import hparams
import json

syncnet_T = 5
syncnet_mel_step_size = 16

class FittingImage(object):
    
    def __init__(self, model_path, save_root, gpu_id, args) -> None:
        super().__init__()
        self.args = args

        self.model_path = model_path

        self.device = torch.device("cuda:%d" % gpu_id)
        self.save_root = save_root
        self.opt_cam = True
        self.view_num = 45
        self.duration = 3.0 / self.view_num
        self.model_name = os.path.basename(model_path)[:-4]

        self.build_info()
        self.build_tool_funcs()

    def on_load_checkpoint(self, ours, checkpoint:dict)->None:
        state_dict = checkpoint
        model_state_dict = ours.state_dict()
        is_changed = False
        for k in state_dict:
            if k in model_state_dict:
                if state_dict[k].shape != model_state_dict[k].shape:
                    print("Skip loading pararmeter")
                    state_dict[k] = model_state_dict[k]
                    is_changed = True
            else:
                print("Dropping parameter")
                is_changed = True

    def build_info(self):

        check_dict = torch.load(self.model_path, map_location=torch.device("cpu"))

        para_dict = check_dict["para"]
        self.opt = BaseOptions(para_dict)

        self.featmap_size = self.opt.featmap_size
        self.pred_img_size = self.opt.pred_img_size
        
        if not os.path.exists(self.save_root): os.mkdir(self.save_root)

        net = HeadNeRFNet(self.opt, include_vd=True, hier_sampling=False)
        self.on_load_checkpoint(net, check_dict['net'])

        self.net = net.to(self.device)
        self.net.eval()

        args.dim_aud = 256
        args.win_size=16
        self.AudNet = AudioNet(args.dim_aud, args.win_size).to(self.device)
        self.AudAttNet = AudioAttNet().to(self.device)

        ckpts = './train_data/base.tar'
        print('Found ckpts', ckpts)
        ckpt = torch.load(ckpts)

        AudNet_state = ckpt['network_audnet_state_dict']
        self.on_load_checkpoint(self.AudNet, AudNet_state)

        if 'network_audattnet_state_dict' in ckpt:
            AudAttNet_state = ckpt['network_audattnet_state_dict']
            self.on_load_checkpoint(self.AudAttNet, AudAttNet_state)



    def build_tool_funcs(self):
        self.loss_utils = HeadNeRFLossUtils(device=self.device)
        self.render_utils = RenderUtils(view_num=45, device=self.device, opt=self.opt)
        
        self.xy = self.render_utils.ray_xy.repeat(syncnet_T,1,1)
        self.uv = self.render_utils.ray_uv.repeat(syncnet_T,1,1)
    
    def crop_audio_window(self, spec, start_frame):
        start_idx = int(80. * (start_frame / float(hparams.fps)))
        end_idx = start_idx + syncnet_mel_step_size #* syncnet_T
        out = spec[start_idx : end_idx, :]
        # out = out.reshape(out.shape[0], -1, syncnet_T)
        return out

    def load_data(self, img_path, mask_path, para_3dmm_path):
        # Idx ----------------------------------------
        with open(os.path.join(self.args.transform_json)) as fp:
            meta = json.load(fp)

        start = meta['frames'][0]['img_id']
        end = meta['frames'][-1]['img_id']
        arr = np.arange(start+syncnet_T//2, end-syncnet_T//2-1)
        perm = np.random.permutation(arr)
        start_idx = perm[0]
        batch_idx = range(start_idx-syncnet_T//2, start_idx+syncnet_T//2+1)

        wav = audio.load_wav('/local_datasets/LipSync/cnn/0/aud.wav', hparams.sample_rate)
        orig_mel = audio.melspectrogram(wav).T # [2699,80]
        mel = self.crop_audio_window(orig_mel.copy(), start_idx)
        self.gt_mel = torch.FloatTensor(mel.T).unsqueeze(0).to(self.device)


        self.aud = [None] * syncnet_T
        self.img_tensor = [None] * syncnet_T
        self.mask_tensor = [None] * syncnet_T
        self.bg_tensor = [None] * syncnet_T
        self.base_c2w_Rmat = [None] * syncnet_T
        self.base_c2w_Tvec = [None] * syncnet_T
        self.temp_inv_inmat = [None] * syncnet_T

        self.base_iden = [None]*syncnet_T
        self.base_expr = [None]*syncnet_T
        self.base_text = [None]*syncnet_T
        self.base_illu = [None]*syncnet_T

        for b_idx, idx in enumerate(batch_idx):

            i = str(idx) + '.png'

            # Audio ----------------------------------------
            aud_file = 'aud.npy'
            aud_features = np.load(os.path.join('./train_data', aud_file))
            auds_ = torch.Tensor(aud_features).to(self.device) # [843,16,29]
            self.smo_size=8
            smo_half_win = int(self.smo_size / 2)
            left_i = idx - smo_half_win
            right_i = idx + smo_half_win
            pad_left, pad_right = 0, 0
            if left_i < 0:
                pad_left = -left_i
                left_i = 0
            if right_i > end:
                pad_right = right_i-end
                right_i = end
            auds_win = auds_[left_i:right_i]
            if pad_left > 0:
                auds_win = torch.cat(
                    (torch.zeros_like(auds_win)[:pad_left], auds_win), dim=0)
            if pad_right > 0:
                auds_win = torch.cat(
                    (auds_win, torch.zeros_like(auds_win)[:pad_right]), dim=0)
            auds_win = self.AudNet(auds_win)
            aud = auds_win[smo_half_win]
            self.aud_i = self.AudAttNet(auds_win).unsqueeze(0)

            # Image & Mask----------------------------------------
            img_size = (self.pred_img_size, self.pred_img_size)
            img_path_ = os.path.join(img_path, i)
            mask_path_ = os.path.join(mask_path, i[:-4]+'_mask'+i[-4:])

            img = cv2.imread(img_path_)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = img.astype(np.float32)/255.0
            
            gt_img_size = img.shape[0]
            if gt_img_size != self.pred_img_size:
                img = cv2.resize(img, dsize=img_size, fx=0, fy=0, interpolation=cv2.INTER_LINEAR)
            
            mask_img = cv2.imread(mask_path_, cv2.IMREAD_UNCHANGED).astype(np.uint8)
            if mask_img.shape[0] != self.pred_img_size:
                mask_img = cv2.resize(mask_img, dsize=img_size, fx=0, fy=0, interpolation=cv2.INTER_NEAREST)
            # img[mask_img < 0.5] = 1.0
            
            self.img_tensor_i = (torch.from_numpy(img).permute(2, 0, 1)).unsqueeze(0).to(self.device)
            self.mask_tensor_i = torch.from_numpy(mask_img[None, :, :]).unsqueeze(0).to(self.device)
            
            bg_name = str(idx) + '.jpg'
            bg_path_ = "/local_datasets/LipSync/cnn/0/torso_imgs"
            bg_path = os.path.join(bg_path_, bg_name)
            bg = cv2.imread(bg_path)
            bg = cv2.cvtColor(bg, cv2.COLOR_BGR2RGB)
            bg_tensor = bg.astype(np.float32)/255.0 # [512,512,3]
            self.bg_tensor_i = (torch.from_numpy(bg_tensor).permute(2, 0, 1)).unsqueeze(0).to(self.device)


            # 3DMM ----------------------------------------
            # load init codes from the results generated by solving 3DMM rendering opt.
            para_3dmm_path_ = os.path.join(para_3dmm_path, i[:-4]+'_nl3dmm.pkl')
            with open(para_3dmm_path_, "rb") as f: nl3dmm_para_dict = pkl.load(f)
            base_code = nl3dmm_para_dict["code"].detach().unsqueeze(0).to(self.device)
            
            base_iden = base_code[:, :self.opt.iden_code_dims]
            base_expr = base_code[:, self.opt.iden_code_dims:self.opt.iden_code_dims + self.opt.expr_code_dims]
            base_text = base_code[:, self.opt.iden_code_dims + self.opt.expr_code_dims:self.opt.iden_code_dims 
                                                                + self.opt.expr_code_dims + self.opt.text_code_dims]
            base_illu = base_code[:, self.opt.iden_code_dims + self.opt.expr_code_dims + self.opt.text_code_dims:]
            
            self.base_c2w_Rmat_i = nl3dmm_para_dict["c2w_Rmat"].detach().unsqueeze(0)
            self.base_c2w_Tvec_i = nl3dmm_para_dict["c2w_Tvec"].detach().unsqueeze(0).unsqueeze(-1)
            self.base_w2c_Rmat = nl3dmm_para_dict["w2c_Rmat"].detach().unsqueeze(0)
            self.base_w2c_Tvec = nl3dmm_para_dict["w2c_Tvec"].detach().unsqueeze(0).unsqueeze(-1)

            temp_inmat = nl3dmm_para_dict["inmat"].detach().unsqueeze(0)
            temp_inmat[:, :2, :] *= (self.featmap_size / gt_img_size)
            
            temp_inv_inmat = torch.zeros_like(temp_inmat)
            temp_inv_inmat[:, 0, 0] = 1.0 / temp_inmat[:, 0, 0]
            temp_inv_inmat[:, 1, 1] = 1.0 / temp_inmat[:, 1, 1]
            temp_inv_inmat[:, 0, 2] = -(temp_inmat[:, 0, 2] / temp_inmat[:, 0, 0])
            temp_inv_inmat[:, 1, 2] = -(temp_inmat[:, 1, 2] / temp_inmat[:, 1, 1])
            temp_inv_inmat[:, 2, 2] = 1.0
            
            self.temp_inmat = temp_inmat
            self.temp_inv_inmat_i = temp_inv_inmat

            self.aud[b_idx] = self.aud_i
            self.img_tensor[b_idx] = self.img_tensor_i
            self.mask_tensor[b_idx] = self.mask_tensor_i
            self.bg_tensor[b_idx] = self.bg_tensor_i
            self.base_c2w_Rmat[b_idx] = self.base_c2w_Rmat_i
            self.base_c2w_Tvec[b_idx] = self.base_c2w_Tvec_i
            self.temp_inv_inmat[b_idx] = self.temp_inv_inmat_i

            self.base_iden[b_idx] = base_iden
            self.base_expr[b_idx] = base_expr
            self.base_text[b_idx] = base_text
            self.base_illu[b_idx] = base_illu

        self.aud = torch.cat(self.aud, dim=0) # [5,256]
        self.img_tensor = torch.cat(self.img_tensor, dim=0) # [5,3,512,512]
        self.mask_tensor = torch.cat(self.mask_tensor, dim=0) # [5,1,512,512]
        self.bg_tensor = torch.cat(self.bg_tensor, dim=0) # [5,3,512,512]
        self.base_c2w_Rmat = torch.cat(self.base_c2w_Rmat, dim=0) # [5,3,3]
        self.base_c2w_Tvec = torch.cat(self.base_c2w_Tvec, dim=0) # [5,3,1]
        self.temp_inv_inmat = torch.cat(self.temp_inv_inmat, dim=0) # [5,3,3

        self.base_iden = torch.cat(self.base_iden, dim=0)
        self.base_expr = torch.cat(self.base_expr, dim=0)
        self.base_text = torch.cat(self.base_text, dim=0)
        self.base_illu = torch.cat(self.base_illu, dim=0)

        self.cam_info = {
            "batch_Rmats": self.base_c2w_Rmat.to(self.device),
            "batch_Tvecs": self.base_c2w_Tvec.to(self.device),
            "batch_inv_inmats": self.temp_inv_inmat.to(self.device)
        }
        

    @staticmethod
    def eulurangle2Rmat(angles):
        batch_size = angles.size(0)
        
        sinx = torch.sin(angles[:, 0])
        siny = torch.sin(angles[:, 1])
        sinz = torch.sin(angles[:, 2])
        cosx = torch.cos(angles[:, 0])
        cosy = torch.cos(angles[:, 1])
        cosz = torch.cos(angles[:, 2])

        rotXs = torch.eye(3, device=angles.device).view(1, 3, 3).repeat(batch_size, 1, 1)
        rotYs = rotXs.clone()
        rotZs = rotXs.clone()
        
        rotXs[:, 1, 1] = cosx
        rotXs[:, 1, 2] = -sinx
        rotXs[:, 2, 1] = sinx
        rotXs[:, 2, 2] = cosx
        
        rotYs[:, 0, 0] = cosy
        rotYs[:, 0, 2] = siny
        rotYs[:, 2, 0] = -siny
        rotYs[:, 2, 2] = cosy

        rotZs[:, 0, 0] = cosz
        rotZs[:, 0, 1] = -sinz
        rotZs[:, 1, 0] = sinz
        rotZs[:, 1, 1] = cosz
        
        res = rotZs.bmm(rotYs.bmm(rotXs))
        return res
    
    
    def build_code_and_cam(self):
        
        # code
        shape_code = torch.cat([self.base_iden + self.iden_offset, self.base_expr + self.expr_offset], dim=-1)
        appea_code = torch.cat([self.base_text, self.base_illu], dim=-1) + self.appea_offset
        
        opt_code_dict = {
            "bg":None,
            "iden":self.iden_offset,
            "expr":self.expr_offset,
            "appea":self.appea_offset
        }
        
        code_info = {
            "bg_code": None, 
            "shape_code":shape_code, 
            "appea_code":appea_code, 
        }

        
        #cam
        if self.opt_cam:
            delta_cam_info = {
                "delta_eulur": self.delta_EulurAngles, 
                "delta_tvec": self.delta_Tvecs
            }

            batch_delta_Rmats = self.eulurangle2Rmat(self.delta_EulurAngles).repeat(syncnet_T,1,1)
            base_Rmats = self.cam_info["batch_Rmats"]
            base_Tvecs = self.cam_info["batch_Tvecs"]
            
            cur_Rmats = batch_delta_Rmats.bmm(base_Rmats)
            cur_Tvecs = batch_delta_Rmats.bmm(base_Tvecs) + self.delta_Tvecs
            
            batch_inv_inmat = self.cam_info["batch_inv_inmats"] #[N, 3, 3]    
            batch_cam_info = {
                "batch_Rmats": cur_Rmats,
                "batch_Tvecs": cur_Tvecs,
                "batch_inv_inmats": batch_inv_inmat
            }
            
        else:
            delta_cam_info = None
            batch_cam_info = self.cam_info


        return code_info, opt_code_dict, batch_cam_info, delta_cam_info
    
    
    @staticmethod
    def enable_gradient(tensor_list):
        for ele in tensor_list:
            ele.requires_grad = True


    def perform_fitting(self,img_path, mask_path, para_3dmm_path, base_name, save_root):
        self.delta_EulurAngles = torch.zeros((1, 3), dtype=torch.float32).to(self.device)
        self.delta_Tvecs = torch.zeros((1, 3, 1), dtype=torch.float32).to(self.device)

        self.iden_offset = torch.zeros((1, 100), dtype=torch.float32).to(self.device)
        self.expr_offset = torch.zeros((1, 79), dtype=torch.float32).to(self.device)
        self.appea_offset = torch.zeros((1, 127), dtype=torch.float32).to(self.device)
        self.enable_gradient([*self.net.parameters(), *self.AudNet.parameters(), *self.AudAttNet.parameters()])
        if self.opt_cam:
            self.enable_gradient(
                [self.iden_offset, self.expr_offset, self.appea_offset, self.delta_EulurAngles, self.delta_Tvecs]
            )
        else:
            self.enable_gradient(
                [self.iden_offset, self.expr_offset, self.appea_offset]
            )
        
        init_learn_rate = 0.01
        step_decay = self.args.step_decay #100000//syncnet_T #40000
        iter_num = self.args.iter_num #100000//syncnet_T #40000
        
        params_group = [
            {'params': [self.iden_offset], 'lr': init_learn_rate * 1.5},
            {'params': [self.expr_offset], 'lr': init_learn_rate * 1.5},
            {'params': [self.appea_offset], 'lr': init_learn_rate * 1.0},
            {'params': [*self.net.parameters()], 'lr': init_learn_rate * 0.01},
            {'params': [*self.AudNet.parameters()], 'lr': 5e-4},
            {'params': [*self.AudAttNet.parameters()], 'lr': 5e-4},
        ]
        
        if self.opt_cam:
            params_group += [
                {'params': [self.delta_EulurAngles], 'lr': init_learn_rate * 0.1},
                {'params': [self.delta_Tvecs], 'lr': init_learn_rate * 0.1},
            ]

        optimizer = torch.optim.Adam(params_group, betas=(0.9, 0.999))
        lr_func = lambda epoch: 0.1 ** (epoch / step_decay)
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_func)
        
        loop_bar = tqdm(range(iter_num), leave=True)
        for iter_ in loop_bar:
            with torch.set_grad_enabled(True):

                self.load_data(img_path, mask_path, para_3dmm_path)

                code_info, opt_code_dict, cam_info, delta_cam_info = self.build_code_and_cam()

                pred_dict = self.net("test", self.xy, self.uv, self.aud, self.bg_tensor, self.mask_tensor, **code_info, **cam_info)

                batch_loss_dict = self.loss_utils.calc_total_loss(iter_,
                    delta_cam_info=delta_cam_info, opt_code_dict=opt_code_dict, pred_dict=pred_dict, 
                    gt_rgb=self.img_tensor, gt_mel=self.gt_mel, mask_tensor=self.mask_tensor
                )

            optimizer.zero_grad()
            batch_loss_dict["total_loss"].backward()
            # batch_loss_dict["head_loss"].backward()
            optimizer.step()
            scheduler.step()   
            loop_bar.set_description("Opt, Loss: %.6f  " % batch_loss_dict["head_loss"].item())          


            self.opt_code_dict = opt_code_dict
            self.delta_cam_info = delta_cam_info
            
            if iter_ % 1000 == 0:
                print("==== Saved ====")
                self.save_res(base_name, save_root, iter_)

                coarse_fg_rgb = pred_dict["coarse_dict"]["merge_img"]
                coarse_fg_rgb = (coarse_fg_rgb[0].detach().cpu().permute(1, 2, 0).numpy()* 255).astype(np.uint8)
                cv2.imwrite("./train_result/img_%04d.png" % iter_, coarse_fg_rgb[:, :, ::-1])  
        self.opt_code_dict = opt_code_dict
        self.delta_cam_info = delta_cam_info


    def save_res(self, base_name, save_root, iter):
        code_dict = {
            "code": self.opt_code_dict
        }
        cam_dict = {
            "cam": self.delta_cam_info
        }
        model_dict = {
            "headnerf": self.net.state_dict(),
            "audnet": self.AudNet.state_dict(),
            "audattnet": self.AudAttNet.state_dict()
        }
        torch.save(code_dict, "%s/Code_%s_%s_%s.pth" % (save_root, base_name, str(iter), self.model_name))
        torch.save(cam_dict, "%s/Cam_%s_%s_%s.pth" % (save_root, base_name, str(iter), self.model_name))
        torch.save(model_dict, "%s/HeadNeRF_%s_%s_%s.pth" % (save_root, base_name, str(iter), self.model_name))


    def fitting_single_images(self, img_path, mask_path, para_3dmm_path, tar_code_path, save_root):

        base_name = os.path.basename(img_path)[4:-4]

        # load tar code
        if tar_code_path is not None:
            temp_dict = torch.load(tar_code_path, map_location="cpu")
            tar_code_info = temp_dict["code"]
            for k, v in tar_code_info.items():
                if v is not None:
                    tar_code_info[k] = v.to(self.device)
            self.tar_code_info = tar_code_info
        else:
            self.tar_code_info = None

        self.perform_fitting(img_path, mask_path, para_3dmm_path, base_name, save_root)
        self.save_res(base_name, save_root, "final")
        

if __name__ == "__main__":

    torch.manual_seed(45)  # cpu
    torch.cuda.manual_seed(55)  # gpu
    np.random.seed(65)  # numpy
    random.seed(75)  # random and transforms
    # torch.backends.cudnn.deterministic = True  # cudnn
    # torch.backends.cudnn.benchmark = True
    # torch.backends.cuda.matmul.allow_tf32 = False
    # torch.backends.cudnn.allow_tf32 = False

    parser = argparse.ArgumentParser(description='a framework for fitting a single image using HeadNeRF')
    parser.add_argument("--model_path", type=str, required=True)
    
    parser.add_argument("--img", type=str, required=True)
    parser.add_argument("--mask", type=str, required=True)
    parser.add_argument("--para_3dmm", type=str, required=True)
    parser.add_argument("--save_root", type=str, required=True)
    
    parser.add_argument("--target_embedding", type=str, default="")

    parser.add_argument("--transform_json", type=str, default='/local_datasets/LipSync/cnn/0/transforms_train.json')

    parser.add_argument('--step_decay', type=int, default=50000)
    parser.add_argument('--iter_num', type=int, default=50000)
    
    args = parser.parse_args()


    model_path = args.model_path
    save_root = args.save_root
    
    img_path = args.img
    mask_path = args.mask
    para_3dmm_path = args.para_3dmm
    
    if len(args.target_embedding) == 0:
        target_embedding_path = None
    else:
        target_embedding_path = args.target_embedding
    
        temp_str_list = target_embedding_path.split("/")
        if temp_str_list[1] == "*":
            temp_str_list[1] = os.path.basename(model_path)[:-4]
            target_embedding_path = os.path.join(*temp_str_list)
        
        assert os.path.exists(target_embedding_path)
    tt = FittingImage(model_path, save_root, gpu_id=0, args=args)
    tt.fitting_single_images(img_path, mask_path, para_3dmm_path, target_embedding_path, save_root)
