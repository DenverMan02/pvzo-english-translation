;(function($) {  
	$.fn.extend({
		'wScrollLayer':function(options){
			options = $.extend({
				container : '.content',
				speed : 300,
				margintop : 50,
				autohide:false,
				scrolling:true,
				btmlimit:false,
				layerref:'.content'//limit-top
				//layerref2:'.content'//limit-bottom
				},options);
	this.each(function(){
		var $container = $(options.container);
		var $layerRef = $(options.layerref)
		
		var $this = $(this);
		var winH = parseInt($(window).height());
		var layerH = parseInt($this.height());
		var layerRefT = parseInt($layerRef.offset().top);
		
		var layerTop = parseInt(options.margintop);
		$layerRef.css('position','relative')
		$this.css('top',layerTop);
		if(options.autohide){
			$this.hide();
		}
		var btmlimitH = 0
		var btmlimitT = 0
		var layerrefT = parseInt($layerRef.offset().top)
		if(options.btmlimit){
			var $btmlimit = $(options.btmlimit)
			btmlimitH = parseInt($btmlimit.height())
			btmlimitT = parseInt($btmlimit.offset().top)
		}
		var conH = btmlimitH+btmlimitT-layerrefT;

		var maxTop = conH-layerH;
		var minTop = options.margintop;
		$(window).scroll(function (){
			
			var winScr = $(window).scrollTop();
			if (winScr>=layerRefT){
				var offsetTemp = winScr-layerRefT+layerTop
				if(options.btmlimit){
					if(offsetTemp >= maxTop){
						offsetTemp = maxTop
					}
					/*
					else if(offsetTemp < minTop){
						offsetTemp = minTop
					}
					*/
				}
				if(offsetTemp < minTop){
					offsetTemp = minTop
				}
				var offsetTop = offsetTemp;
				if(options.autohide){
					$this.fadeIn();
				}
				
			}
			else{
				var offsetTop = layerTop;
				if(options.autohide){
					$this.fadeOut();
				}
			}
			if(options.scrolling){
				$this.animate({'top':offsetTop+'px'},{duration:options.speed , queue:false});
			}
			else{
				$this.css('top',offsetTop+'px');
			}
	
		});
	})
		}
	})
})(jQuery);
	