/* Original test-only libgpiod 2.x ABI simulator. Never opens a GPIO device. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>

typedef struct { int direction, bias, low, value; unsigned offset; } Settings;
typedef struct { Settings lines[2]; int count, lock; } Lines;
static char base[4096];
static void paths(void) { if(!base[0]) { Dl_info info; dladdr((void *)paths,&info); snprintf(base,sizeof(base),"%s",info.dli_fname); } }
static void logop(const char *action,int offset,int value) {
    paths(); char path[4200]; snprintf(path,sizeof(path),"%s.calls",base);
    FILE *out=fopen(path,"a"); if(out) { fprintf(out,"%d %s %d %d\n",getpid(),action,offset,value); fclose(out); }
}
static void state(int *level,char *mode) {
    paths(); char path[4200]; snprintf(path,sizeof(path),"%s.state",base);
    *level=1; strcpy(mode,"ok"); FILE *in=fopen(path,"r");
    if(in) { if(fscanf(in,"%d %31s",level,mode)!=2) { *level=1; strcpy(mode,"ok"); } fclose(in); }
}
void *gpiod_chip_open(const char *path) {
    if(strcmp(path,"/dev/gpiochip0")) { errno=ENOENT; return NULL; }
    logop("OPEN",0,0); return calloc(1,1);
}
void gpiod_chip_close(void *chip) { logop("CLOSE",0,0); free(chip); }
void *gpiod_line_settings_new(void) { return calloc(1,sizeof(Settings)); }
void gpiod_line_settings_free(void *s) { free(s); }
int gpiod_line_settings_set_direction(Settings *s,int direction) { s->direction=direction; return 0; }
int gpiod_line_settings_set_bias(Settings *s,int bias) { s->bias=bias; return 0; }
void gpiod_line_settings_set_active_low(Settings *s,bool low) { s->low=low; }
int gpiod_line_settings_set_output_value(Settings *s,int value) { s->value=value; return 0; }
void *gpiod_line_config_new(void) { return calloc(1,sizeof(Lines)); }
void gpiod_line_config_free(void *c) { free(c); }
int gpiod_line_config_add_line_settings(Lines *c,const unsigned *offsets,size_t count,Settings *s) {
    if(count!=1 || c->count>=2 || offsets[0]>31 || (s->direction!=2 && s->direction!=3)) { errno=EINVAL; return -1; }
    c->lines[c->count]=*s; c->lines[c->count++].offset=offsets[0]; return 0;
}
void *gpiod_request_config_new(void) { return calloc(1,64); }
void gpiod_request_config_free(void *c) { free(c); }
void gpiod_request_config_set_consumer(char *c,const char *name) { snprintf(c,64,"%s",name); }
void *gpiod_chip_request_lines(void *chip,const char *c,Lines *lines) {
    (void)chip; int level; char mode[32]; state(&level,mode);
    if(strcmp(c,"bloxsmith-button-led") || !strcmp(mode,"busy")) { errno=EBUSY; return NULL; }
    char path[4200]; snprintf(path,sizeof(path),"%s.lock",base);
    int lock=open(path,O_RDWR|O_CREAT,0600);
    if(lock<0) return NULL;
    if(flock(lock,LOCK_EX|LOCK_NB)<0) { close(lock); return NULL; }
    Lines *r=malloc(sizeof(Lines)); *r=*lines; r->lock=lock; logop("REQUEST",0,0);
    for(int i=0;i<r->count;i++) {
        Settings *s=&r->lines[i];
        logop("DIRECTION",s->offset,s->direction); logop("BIAS",s->offset,s->bias); logop("LOW",s->offset,s->low);
        if(s->direction==3) logop("INITIAL",s->offset,s->value^s->low);
    }
    return r;
}
int gpiod_line_request_get_value(Lines *r,unsigned offset) {
    int level; char mode[32]; state(&level,mode);
    if(!strcmp(mode,"hang")) { logop("HANG",0,0); sleep(30); }
    if(!strcmp(mode,"error")) { errno=EIO; return -1; }
    for(int i=0;i<r->count;i++) if(r->lines[i].offset==offset && r->lines[i].direction==2) return level^r->lines[i].low;
    errno=EINVAL; return -1;
}
int gpiod_line_request_set_value(Lines *r,unsigned offset,int value) {
    for(int i=0;i<r->count;i++) if(r->lines[i].offset==offset && r->lines[i].direction==3) {
        r->lines[i].value=value; logop("WRITE",offset,value^r->lines[i].low); return 0;
    }
    logop("BAD_WRITE",offset,value); errno=EINVAL; return -1;
}
void gpiod_line_request_release(Lines *r) { logop("RELEASE",0,0); close(r->lock); free(r); }
