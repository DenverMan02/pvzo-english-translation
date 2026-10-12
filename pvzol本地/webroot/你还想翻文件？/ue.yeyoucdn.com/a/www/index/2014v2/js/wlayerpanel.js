;(function($) {  
	$.fn.extend({
		'wLayerPanel':function(options){
			options = $.extend({
				panel:'.js-layer-panel0',
				box:'.js-list',
				element:'li',
				showCon:'.js-layer-con0',
				eventShow:'mouseenter',
				eventHide:'mouseleave',
				showPos:'auto',
				xPos:0,
				yPos:0
			},options);
			/*this.each*/
			this.each(function(){
				var $this = $(this);
				var $show = $(options.panel,$this);
				var showPaddingL = parseInt($show.css('padding-left'));
				var showPaddingR = parseInt($show.css('padding-right'));
				var showPaddingT = parseInt($show.css('padding-top'));
				var showPaddingB = parseInt($show.css('padding-bottom'));
				var layerW = parseInt($show.width());
				var layerH = parseInt($show.height());
				var alphaW = parseInt($this.width()-layerW);
				var alphaH = parseInt($this.height()-layerH);
				var $item;
				
				$(options.box).delegate(options.element,options.eventShow,function(e){
					$item = $(this);
					$show.html($(options.showCon,$item).html());
					layerH = parseInt($show.height());
					alphaH = parseInt($this.height()-layerH);
		
					var modW = parseInt($item.width());
					var modH = parseInt($item.height());
					var modX = parseInt($item.offset().left);
					var modY = parseInt($item.offset().top);
					var boxX = parseInt($this.offset().left);
					var boxY = parseInt($this.offset().top);
					var layerX = 0;
					var layerY = 0;
					
					if(options.showPos == 'auto'){
						layerX = modX - boxX + modW + options.xPos;
						layerY = modY - boxY + modH + options.yPos;
			
						if( layerX >= alphaW ){
							layerX = modX - boxX - layerW - showPaddingL - showPaddingR + options.xPos;
							
						}
						if( layerY >= alphaH ){
							layerY = modY - boxY - layerH + modH - showPaddingT - showPaddingB + options.yPos;
						}
					}
					else if( options.showPos == 'right'){
						layerX = modX - boxX + modW + options.xPos;
						layerY = modY - boxY + options.yPos;
					}
					else if( options.showPos == 'left'){
						layerX = modX - boxX - layerW - showPaddingL - showPaddingR + options.xPos;
						layerY = modY - boxY + options.yPos;
					}
					else if( options.showPos == 'center'){
						layerX = modX - boxX + options.xPos;
						layerY = modY - boxY + options.yPos;
					}
					else if( options.showPos == 'bottom'){
						layerX = modX - boxX + options.xPos;
						layerY = modY - boxY + options.yPos+modH;
					}
		
					$show.css({left:layerX+'px',top:layerY+'px'});
		
					$show.show();
					$item.addClass('item-show');
					$item.siblings().removeClass('item-show');
					$(options.panel).mouseleave(function(){
						$item.removeClass('item-show');
					})
					$show.mouseenter(function(){
						$(this).show();
					})
					$show.mouseleave(function(){
						$(this).hide();
					})
				}).delegate(options.element,options.eventHide,function(){
					$show.hide();
				})
				
			})
			/*/this.each*/	
		}
	})
})(jQuery); 