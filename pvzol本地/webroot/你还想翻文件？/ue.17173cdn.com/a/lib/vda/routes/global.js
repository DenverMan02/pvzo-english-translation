(function(){
	if(/wegame/gi.test(navigator.userAgent)){return;}
	advConfigs.loadConfigFile([
	  {
		filter: function () {
		  return /newgame\.17173\.com\/game-info/.test(location.href);
		},
		configs: ['https://s.17173cdn.com/global/newgamebihua.js']
	  },
	  {
		filter: function () {
		  return /(newgame\.17173\.com\/zt\/game-info)|(newgame\.17173\.com\/game-demo)/.test(location.href);
		},
		configs: ['//s.17173cdn.com/newgame/final.js','//s.17173cdn.com/global/tuiguangbottombanner.js']
	  },
	  {
		filter: function () {
		  var isNewsContent = /^(http|https):\/\/news\.17173\.com\/content\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml/.test(location.href);
		  var isZhuanquContent = /(\/z\/.*?content)|(lol|dnf|jx3|wow|xyq|tl|hs|bns|moyu|mu|cf|poe|nsh|ff14|pubg|cqyh|mir|gw2|csgo|d3|).+?(content|news)/.test(location.href);
		  return isNewsContent || isZhuanquContent;
		},
		configs: ['//s.17173cdn.com/global/finalrightvideo.js']
	  },
	  {
		filter: function () {
		  return /news\.17173\.com\/content\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml|news\.17173\.com\/c\/bc\/content\//.test(
			location.href
		  );
		},
		configs: ['//s.17173cdn.com/news/final-all.js','//s.17173cdn.com/news/final.js','https://www.17173.com/2015/new/news-v-daka.js']
	  },
	  {
		filter: function () {
		  return (
			/\/\/bbs\.17173\.com\/forum/.test(location.href) ||
			/\/\/bbs\.17173\.com\/thread-/.test(location.href)
		  );
		},
		configs: ['//s.17173cdn.com/bbs/list-final.js']
	  },
	  {
		filter: function () {
		  return location.hostname == 'v.17173.com';
		},
		configs: ['https://s.17173cdn.com/vlog/media-global.js']
	  },
	  {
		filter: function () {
		  return location.hostname == 'v.17173.com' && location.pathname == '/';
		},
		configs: ['https://s.17173cdn.com/vlog/media-index.js']
	  },
	  {
		filter: function () {
		  return (
			location.hostname == 'v.17173.com' && /^\/v_/.test(location.pathname)
		  );
		},
		configs: ['https://s.17173cdn.com/vlog/media-final.js']
	  },
	  {
		filter: function () {
		  var isZqModuleList = location.hostname == 'news.17173.com' && /morelist/gi.test(location.href) && !!ued.pageInfo,
			isZqModuleFinal = location.hostname == 'news.17173.com' && /content\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml/.test(location.href) && !!ued.pageInfo,
			isNewgameFinal = /newgame\.17173\.com(\/zt\/|\/)game-info(-|\/)/gi.test(location.href),
			isHaoFinal = /hao\.17173\.com\/game-info-\d+\.html/gi.test(location.href);
		  return isZqModuleList || isZqModuleFinal || isNewgameFinal || isHaoFinal;
		},
		configs: ['https://s.17173cdn.com/global/newgame-zq.js']
	  },
	  {
		filter: function () {
		  var isNewsZq = /\/z\/(tym|ys|pvp|yys)($|\/$|.*?content)/gi.test(location.href),
			  isNewsZqFinal = /(news|dnf|poe|lol|wow|xyq|tl|jx3|bns|ff14|hs|xjzj)\.17173\.com($|\/$|\/content|\/news)\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml/gi.test(location.href),
			  isNewsFinal = /news\.17173\.com\/content\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml/.test(location.href),
			  isNewgameFinal = /newgame\.17173\.com\/game-info-\d+\.html/.test(location.href),
			  isHaoFinal = /hao\.17173\.com\/gift-info-\d+\.html/.test(location.href),
			  isVFinal = /v\.17173\.com\/v_.*?\.html/.test(location.href),
			  isBBS = /bbs\.17173\.com\/(forum|thread)-.*?\.html/gi.test(location.href),
			  isNewsList = /news\.17173\.com\/(newgame|magazine|dalu|quanqiu|pl|renwu|topic|ana|zf|law|top|gameview|world|game|daguan|lzs|wjhyl|caibao)+(\/)/gi.test(location.href);
		  return isNewsFinal || isNewgameFinal || isHaoFinal || isVFinal || isBBS || isNewsList;
		},
		configs: ['//s.17173cdn.com/global/topbar.js']
	  },
	  {
		filter: function () {
		  var isNewsNewgameFinal = /(newgame)\.17173\.com\/content\/[a-zA-Z\d\-]+\/[a-zA-Z\d\_]+\.shtml/gi.test(location.href),
			  isNewgamePingce = /newgame\.17173\.com\/game-demo-\d+\.html/gi.test(location.href);
		  return isNewsNewgameFinal || isNewgamePingce;
		},
		configs: ['https://ue.17173cdn.com/a/lib/vda/sources/17173appqrcode/js/index.js']
	  },
	  {
		filter: function () {
		  return /((xyq|dnf|lol|wow|jx3|bns|ff14|hs|xjzj|zxsj)\.17173)|(\.com\/z\/)/gi.test(location.href)
		},
		configs: ['https://ue.17173cdn.com/a/lib/vda/sources/tg/routes/index.js','//s.17173cdn.com/global/tuiguangbottombanner.js']
	  },
	  {
		filter: function () {
		  return /www\.17173\.com\/d\/(ceshibiao|hj)/gi.test(location.href)
		},
		configs: ['//s.17173cdn.com/newgame/testlist.js']
	  }
	]);
})();

