;(function($) {  
	$.fn.extend({
		'wTab':function(options){
			options = $.extend({
				startfrom : 1,
				autoplay : false,
				neednumber : false,
				needblock:false,
				event:'mouseenter',
				fading:false,
				loadmedia:false,
				btncontrol:false,
				tabblock:'a'
				},options);
		/*this.each*/
		this.each(function(){
			var $jsTab = $(this);
			var $gbTab = $('.gb-tab',$jsTab);
			var $gbTabPn = $('.gb-tab-pn',$jsTab);
			var $tabCon = $gbTabPn.children('.tab-con');
			var $btn = $('.btn-ctrl',$jsTab);
			$btn.hide();
			if(options.btncontrol){
				$btn.show();
			}
			var btnCountAdd = 1
			var $btnPrev = $('.btn-prev',$jsTab);
			var $btnNext = $('.btn-next',$jsTab);

			var gbTabFill = function(){
				var itemCount2 = parseInt($tabCon.length);
				$gbTab.empty();
				for(i=1; i<=itemCount2; i++){
					$gbTab.append('<a class="tab-tit" href="javascript:;">'+i+'</a>')
				}
			}
			var gbTabFill2 = function(){
				var itemCount2 = parseInt($tabCon.length);
				$gbTab.empty();
				for(i=1; i<=itemCount2; i++){
					$gbTab.append('<a class="tab-tit" href="javascript:;"></a>')
				}
			}
			if(options.neednumber){
				gbTabFill();/*gb-tab fill or not*/
			}
			else if(options.needblock){
				gbTabFill2();
			}

			var $gbTabLi = $(options.tabblock,$gbTab);
				
			var itemCount = options.startfrom;
			var itemLength = $gbTabLi.length;
			var moveTimeout;
			var moving = false;
			if(options.autoplay){var timeCount = options.autoplay;}
			if(options.loadmedia){var $container = $('.con-container',$jsTab);}
			
			$tabCon.hide();
			
			var slideshow = function(){
				$($tabCon.get(itemCount-1)).siblings().hide();
				$($tabCon.get(itemCount-1)).siblings().removeClass('curr');
				
				if(options.fading){$($tabCon.get(itemCount-1)).fadeIn();}
				else {$($tabCon.get(itemCount-1)).show();}
				$($tabCon.get(itemCount-1)).addClass('curr')
				
				$($gbTabLi.get(itemCount-1)).siblings().removeClass('on');
				$($gbTabLi.get(itemCount-1)).addClass('on');
				
				if(options.loadmedia){
					$tabCon.empty();
					$($tabCon.get(itemCount-1)).html( $($container.get(itemCount-1)).html() );
				}

				itemCount += 1;
			
				if (itemCount >itemLength){
					itemCount =1;
				}
			
				moving=false;
			}
			
			slideshow();
			
			var sliderotate = function(){
				if (!moving){
					moving=true;
					slideshow();
					clearTimeout(moveTimeout);
					moveTimeout = setTimeout(sliderotate,timeCount);
				}
			}
			
			$gbTabLi.each(function(i){
				$(this).bind(
					options.event,function(){
						itemCount = i+1;
						slideshow();
					}
				)
			})
			
			$btnPrev.click(function(){
				if (itemCount<=1){itemCount = $tabCon.length-1}
				else{itemCount -= 2;}
				slideshow();
			})
			$btnNext.click(function(){
				itemCount;
				slideshow();
			})

			var autoPlay = function(){
				moveTimeout = setTimeout(sliderotate,timeCount);
				$jsTab.hover(
					function(){
						clearTimeout(moveTimeout);
						moveTimeout = false
						moving=false;
					},
					function(){
						if(!moveTimeout){
						moveTimeout = setTimeout(sliderotate,timeCount);
					};
				})
			}
			if (options.autoplay){
				autoPlay();/*auto or not*/
			}
			})
			
		/*/this.each*/	
		}
	})
})(jQuery);  
