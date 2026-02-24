<<<<<<<< HEAD:frontend/dist/assets/oi-profile-B4euG-u2.js
import{w as a}from"./index-C51tMR0S.js";const t={getProfileData:async e=>(await a.post("/oiprofile/api/profile-data",e)).data,getIntervals:async()=>(await a.get("/oiprofile/api/intervals")).data,getUnderlyings:async e=>(await a.get(`/search/api/underlyings?exchange=${e}`)).data,getExpiries:async(e,s)=>(await a.get(`/search/api/expiries?exchange=${e}&underlying=${s}`)).data};export{t as o};
========
import{w as a}from"./index-lxQU7X8J.js";const t={getProfileData:async e=>(await a.post("/oiprofile/api/profile-data",e)).data,getIntervals:async()=>(await a.get("/oiprofile/api/intervals")).data,getUnderlyings:async e=>(await a.get(`/search/api/underlyings?exchange=${e}`)).data,getExpiries:async(e,s)=>(await a.get(`/search/api/expiries?exchange=${e}&underlying=${s}`)).data};export{t as o};
>>>>>>>> upstream/main:frontend/dist/assets/oi-profile-BzOXUEt8.js
