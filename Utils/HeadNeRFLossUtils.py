import cv2
import torch
import torch.nn.functional as F
import torchvision
from torch import nn
from Utils.syncnet import SyncNet_color as SyncNet
import torch.nn.functional as nnf
from collections import OrderedDict
# import lpips
# class VGGPerceptualLoss(torch.nn.Module):
#     def __init__(self, resize=True):
#         super(VGGPerceptualLoss, self).__init__()
#         self.vgg_loss_fn = lpips.LPIPS(net='vgg')


#     def forward(self, input, target,):
#         res = self.vgg_loss_fn(input, target)
#         return res.mean()



class VGGPerceptualLoss(torch.nn.Module):
    def __init__(self, resize=True):
        super(VGGPerceptualLoss, self).__init__()
        blocks = []
        blocks.append(torchvision.models.vgg16(pretrained=True).features[:4].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[4:9].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[9:16].eval())
        blocks.append(torchvision.models.vgg16(pretrained=True).features[16:23].eval())
        for bl in blocks:
            for p in bl.parameters():
                p.requires_grad = False
        self.blocks = torch.nn.ModuleList(blocks)
        self.transform = torch.nn.functional.interpolate
        self.resize = resize
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))


    def forward(self, input, target, feature_layers=[0, 1, 2, 3], style_layers=[]):
        if input.shape[1] != 3:
            input = input.repeat(1, 3, 1, 1)
            target = target.repeat(1, 3, 1, 1)
        input = (input-self.mean) / self.std
        target = (target-self.mean) / self.std
        if self.resize:
            input = self.transform(input, mode='bilinear', size=(224, 224), align_corners=False)
            target = self.transform(target, mode='bilinear', size=(224, 224), align_corners=False)
        loss = 0.0
        x = input
        y = target
        for i, block in enumerate(self.blocks):
            x = block(x)
            y = block(y)
            if i in feature_layers:
                loss += torch.nn.functional.l1_loss(x, y)
            if i in style_layers:
                act_x = x.reshape(x.shape[0], x.shape[1], -1)
                act_y = y.reshape(y.shape[0], y.shape[1], -1)
                gram_x = act_x @ act_x.permute(0, 2, 1)
                gram_y = act_y @ act_y.permute(0, 2, 1)
                loss += torch.nn.functional.l1_loss(gram_x, gram_y)
        return loss

syncnet_T=5
logloss = nn.BCELoss()
def cosine_loss(a, v, y):
    d = nn.functional.cosine_similarity(a, v)
    loss = logloss(d.unsqueeze(1), y)

    return loss

recon_loss = nn.L1Loss()

class HeadNeRFLossUtils(object):
    
    def __init__(self, bg_type = "white", use_vgg_loss = True, device = None) -> None:
        super().__init__()
        
        if bg_type == "white":
            self.bg_value = 1.0
        elif bg_type == "black":
            self.bg_value = 0.0
        else:
            self.bg_type = None
            print("Error BG type. ")
            exit(0)
            
        self.use_vgg_loss = use_vgg_loss
        if self.use_vgg_loss:
            assert device is not None
            self.device = device
            self.vgg_loss_func = VGGPerceptualLoss(resize = True).to(self.device)

        self.syncnet = SyncNet().to(self.device)
        self.syncnet.eval()
        state_dict = torch.load("lipsync_expert.pth", map_location=torch.device("cpu"))["state_dict"]

        # keys = state_dict.keys()
        # values = state_dict.values()
        # new_keys = []
        # for key in keys:
        #     new_key = key#[7:]    # remove the 'module.'
        #     new_keys.append(new_key)
        # new_dict = OrderedDict(list(zip(new_keys, values)))

        self.syncnet.load_state_dict(state_dict) # strict=False)


        for p in self.syncnet.parameters():
            p.requires_grad = False

    @staticmethod
    def calc_cam_loss(delta_cam_info):
        delta_eulur_loss = torch.mean(delta_cam_info["delta_eulur"] * delta_cam_info["delta_eulur"])
        delta_tvec_loss = torch.mean(delta_cam_info["delta_tvec"] * delta_cam_info["delta_tvec"])
        
        return {
            "delta_eular": delta_eulur_loss, 
            "delta_tvec": delta_tvec_loss
        }
        
        
    def calc_code_loss(self, opt_code_dict):
        
        iden_code_opt = opt_code_dict["iden"]
        expr_code_opt = opt_code_dict["expr"]

        iden_loss = torch.mean(iden_code_opt * iden_code_opt)
        expr_loss = torch.mean(expr_code_opt * expr_code_opt)
        
        appea_loss = torch.mean(opt_code_dict["appea"] * opt_code_dict["appea"])

        bg_code = opt_code_dict["bg"]
        if bg_code is None:
            bg_loss = torch.as_tensor(0.0, dtype=iden_loss.dtype, device=iden_loss.device)
        else:
            bg_loss = torch.mean(bg_code * bg_code)
            
        res_dict = {
            "iden_code":iden_loss,
            "expr_code":expr_loss,
            "appea_code":appea_loss,
            "bg_code":bg_loss
        }
        
        return res_dict

    def get_sync_loss(self, mel, g): # [5,3,512,512]
        g = g[:, :, :, g.size(3)//2:]  # [5,3,512,256]
        g = g.unsqueeze(0).permute(0,2,1,4,3)
        g = torch.cat([g[:, :, i] for i in range(syncnet_T)], dim=1)

        # B, 3 * T, H//2, W

        mel = mel.unsqueeze(0)
        a, v = self.syncnet(mel, g)
        y = torch.ones(g.size(0), 1).float().to(self.device)
        return cosine_loss(a, v, y)

    def calc_data_loss(self, iter_, data_dict, gt_rgb, gt_mel, head_mask_c1b, nonhead_mask_c1b):
        
        bg_value = self.bg_value        
        res_img = data_dict["merge_img"]
        head_mask_c3b = head_mask_c1b.expand(-1, 3, -1, -1)
        head_loss = F.mse_loss(res_img[head_mask_c3b], gt_rgb[head_mask_c3b])
        nonhead_mask_c3b = nonhead_mask_c1b.expand(-1, 3, -1, -1)
        nonhead_loss = F.mse_loss(res_img[nonhead_mask_c3b], gt_rgb[nonhead_mask_c3b])

        res = {
            # "bg_loss": bg_loss,  
            "head_loss": head_loss,  
            "nonhaed_loss": 0.01 * nonhead_loss,
            # "sync_loss": wt *sync_loss
        }

        if self.use_vgg_loss:
            masked_gt_img = gt_rgb.clone()
            
            temp_res_img = res_img
            vgg_loss = self.vgg_loss_func(temp_res_img, masked_gt_img)
            res["vgg"] = vgg_loss

        return res
    

    def calc_total_loss(self, iter_, delta_cam_info, opt_code_dict, pred_dict, gt_rgb, gt_mel, mask_tensor):
        
        # assert delta_cam_info is not None
        head_mask = (mask_tensor >= 0.5)  
        nonhead_mask = (mask_tensor < 0.5)  

        coarse_data_dict = pred_dict["coarse_dict"]
        loss_dict = self.calc_data_loss(iter_, coarse_data_dict, gt_rgb, gt_mel, head_mask, nonhead_mask)
        
        total_loss = 0.0
        for k in loss_dict:
            total_loss += loss_dict[k]
            
        #cam loss
        if delta_cam_info is not None:
            loss_dict.update(self.calc_cam_loss(delta_cam_info))
            total_loss += 0.001 * loss_dict["delta_eular"] + 0.001 * loss_dict["delta_tvec"]

        # code loss
        loss_dict.update(self.calc_code_loss(opt_code_dict))        
        total_loss += 0.001 * loss_dict["iden_code"] + \
                      1.0 * loss_dict["expr_code"] + \
                      0.001 * loss_dict["appea_code"] + \
                      0.01 * loss_dict["bg_code"]

        loss_dict["total_loss"] = total_loss
        return loss_dict