import torch,time,sys
torch.set_num_threads(int(sys.argv[1]))
D=__import__("pathlib").Path(".")
b2=torch.load(D/"b2_compact.pt")
d=b2[0]; tk=d["topk"].long(); s=torch.zeros(16,tk.shape[1],64); s.scatter_add_(2,tk,torch.ones_like(tk,dtype=torch.float32))
m=torch.zeros(16,64)
t0=time.time()
for rep in range(50):
    X=s.clone()
    for t in range(X.shape[1]): X[:,t,:]-=m
print("threads",sys.argv[1],"per-trace-loop ms",(time.time()-t0)/50*1000, "T",X.shape[1])
